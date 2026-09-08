"""Web-search adapter used by live-web chat requests.

The chat message is first reframed into clean search queries (LLM, generic -
see `app.services.llm.query_rewriter`), then fanned out: each query runs
against snippet providers concurrently (Tavily + DuckDuckGo, merged for
independent corroboration), community discussion is covered by a
site-restricted variant when the rewriter emits one, and structured adapters
resolve named entities (Yahoo market series + fundamentals, Wikipedia
grounding) and macro topics (FRED) without regex-guessing. A URL mentioned
in the query is read in full via Tavily Extract (one URL max). Results merge
with per-URL/per-text dedupe. Never raises to /chat.
"""
import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from json import JSONDecodeError
from typing import Any, Optional, Union
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.services.llm.langchain_pipeline import TOOL_CATALOG
from app.services.llm.query_rewriter import (
    is_time_sensitive_query,
    rewrite_search_queries,
)

logger = logging.getLogger(__name__)

# Fast-path aliases so common names skip the symbol-search round trip.
# Canonical company identity per entity (ADR tickers keep multi-company
# comparisons in one currency and one market calendar where possible:
# Tesla->TSLA, BYD->BYDDY (OTC ADR), Toyota->TM (NYSE ADR)).
STOCK_ALIASES = {
    "Tesla": "TSLA",
    "Reliance": "RELIANCE.NS",
    "Apple": "AAPL",
    "Amazon": "AMZN",
    "Nike": "NKE",
    "NVIDIA": "NVDA",
    "Nvidia": "NVDA",
    "AMD": "AMD",
    "Advanced Micro Devices": "AMD",
    "BYD": "BYDDY",
    "Toyota": "TM",
    "Toyota Motor": "TM",
    "Microsoft": "MSFT",
    "Alphabet": "GOOGL",
    "Google": "GOOGL",
    "Meta": "META",
    "Intel": "INTC",
    "Samsung": "005930.KS",
}

MAX_SEARCH_QUERIES = 3
# Entity budget: 3-company comparisons (Tesla/BYD/Toyota) must never lose
# a company to truncation. 4 gives headroom without runaway Yahoo fan-out.
MAX_MARKET_ENTITIES = 4

# Generic financial-intent gate for market-series lookup (intents, not names).
MARKET_INTENT_RE = (
    r"\b(stock|stocks|share|shares|price|prices|market cap|marketcap|"
    r"ticker|ipo|valuation|nasdaq|nyse|nse|bse)\b"
)

# Fundamentals intent: questions about company scale/valuation that a price
# series alone cannot answer (market cap, P/E, revenue).
# NOTE: quoteSummary is a CURRENT snapshot only -- it carries no dates and
# can never satisfy a historical ("last N years") request on its own. Any
# historical revenue/profitability need must ALSO route to financial_history.
FUNDAMENTALS_INTENT_RE = re.compile(
    r"\b(market\s*cap|marketcap|p/?e(\s*ratio)?|valuation|revenue|"
    r"fundamentals|earnings|eps|enterprise\s*value|profit\w*|"
    r"net\s*income|operating\s*margin|net\s*margin|ebitda|gross\s*margin)\b",
    re.IGNORECASE,
)

# Historical intent: the question explicitly wants a multi-year window
# ("last 3 years", "over 3 years", "3-year", "3Y", "trailing 3 years").
# A 1-month series or a current snapshot can never satisfy this -- routing
# must request the multi-year adapters (market_history / financial_history).
HISTORICAL_PERIOD_RE = re.compile(
    r"\b(?:last|past|previous|over(?:\s+the)?|trailing)\s+"
    r"(\d+|one|two|three|four|five|six|seven|ten)\s*[- ]?\s*"
    r"(years?|yrs?|y)\b"
    r"|\b(\d+)\s*[- ]?year\s*(comparison|history|trend|performance)?\b"
    r"|\b(3y|5y|10y)\b"
    r"|\b(last|past)\s+\d+\s*[- ]?y\b",
    re.IGNORECASE,
)

_WORD_NUM_MAP = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "ten": 10,
}


def wants_historical(query_text: str) -> bool:
    """True when the query asks for multi-year history (not a snapshot)."""
    if HISTORICAL_PERIOD_RE.search(query_text or ""):
        return True
    try:
        from app.services.data.comparison import parse_time_range as _parse
        years = _parse(query_text or "").years
        return bool(years and years >= 2)
    except Exception:
        return False


def requested_history_years(query_text: str) -> Optional[int]:
    """Parse the requested window in years ("last 3 years" -> 3)."""
    try:
        from app.services.data.comparison import parse_time_range as _parse
        years = _parse(query_text or "").years
        if years:
            return years
    except Exception:
        pass
    match = HISTORICAL_PERIOD_RE.search(query_text or "")
    if not match:
        return None
    for group in (match.group(1), match.group(3)):
        if group:
            raw = group.strip().lower()
            if raw.isdigit():
                return int(raw)
            if raw in _WORD_NUM_MAP:
                return int(_WORD_NUM_MAP[raw])
    short = (match.group(4) or "").lower()
    if short.endswith("y") and short[:-1].isdigit():
        return int(short[:-1])
    return None

# Macro intent -> FRED series. Generic topics, fixed series ids.
MACRO_INTENT_RE = re.compile(
    r"\b(inflation|cpi|consumer\s*price|interest\s*rate|fed\s*funds?|"
    r"federal\s*funds|unemployment|jobless|jobs\s*report|gdp|"
    r"recession|treasury\s*yield|mortgage\s*rate)\b",
    re.IGNORECASE,
)

FRED_SERIES_MAP: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(inflation|cpi|consumer\s*price)\b", re.IGNORECASE),
     "CPIAUCSL", "US Consumer Price Index"),
    (re.compile(r"\b(unemployment|jobless|jobs\s*report)\b", re.IGNORECASE),
     "UNRATE", "US Unemployment Rate"),
    (re.compile(r"\b(interest\s*rate|fed\s*funds?|federal\s*funds)\b",
                re.IGNORECASE),
     "FEDFUNDS", "Federal Funds Rate"),
    (re.compile(r"\bgdp\b", re.IGNORECASE),
     "GDP", "US Gross Domestic Product"),
]

_HEADERS = {"User-Agent": "BuildifyLabs/1.0"}

# Wikipedia grounding budget: same per-request cap as market entities (cheap,
# free, concurrent - one summary fetch per entity).
MAX_WIKI_ENTITIES = 4
WIKI_SUMMARY_MAX_CHARS = 600

# Tavily Extract budget: one URL per request, content truncated so a long
# article cannot blow the prompt. Fires only on a URL in the raw query.
MAX_EXTRACT_URLS = 1
EXTRACT_MAX_CHARS = 4000
_URL_RE = re.compile(r"https?://[^\s)>\]]+")

# Yahoo crumb handshake state (process-local): quoteSummary/v7 need a crumb
# minted against the session cookie, else 401 "Invalid Crumb". Re-minted at
# most every 30 minutes; any failure degrades to "no fundamentals".
_YAHOO_CRUMB_TTL_SECONDS = 1800
_yahoo_crumb: dict[str, Any] = {"crumb": None, "cookie": "", "at": 0.0}

# Stopwords for the deterministic entity fallback: capitalized tokens matching
# these are almost never tradable entities ("Compare", "Weekly", months).
_FALLBACK_ENTITY_STOPWORDS = frozenset(
    {
        "compare", "comparison", "versus", "vs", "between", "and", "or",
        "the", "a", "an", "over", "last", "month", "week", "year", "quarter",
        "weekly", "monthly", "daily", "average", "averages", "price", "prices",
        "performance", "percentage", "change", "chart", "line", "request",
        "revenue", "revenues", "growth", "profit", "profits", "profitability",
        "income", "incomes", "margin", "margins", "earnings", "sales",
        "loss", "losses", "dividend", "dividends", "return", "returns",
        "statistics", "statistic", "figures", "figure", "fiscal",
        "specify", "exact", "date", "range", "show", "give", "what", "which",
        "how", "is", "are", "for", "with", "from", "january", "february",
        "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december", "jan", "feb", "mar", "apr", "jun",
        "jul", "aug", "sep", "sept", "oct", "nov", "dec", "monday",
        "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "today", "latest", "current",
    }
)

def _union_entities(
    framed_entities: list, query_text: str
) -> list[str]:
    """Merge LLM-framed entities with deterministically decomposed ones.

    The deterministic pass (known-company matching over the raw query)
    recovers companies the LLM rewriter drops. Dedupes case-insensitively,
    preserves first-seen order (LLM first), and never raises.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for entity in list(framed_entities or []):
        name = str(entity or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            merged.append(name)
    try:
        from app.services.data.comparison import detect_entities as _detect
    except Exception:
        _detect = None  # type: ignore
    if _detect is not None:
        try:
            for entity in _detect(query_text or "") or []:
                name = str(entity or "").strip()
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    merged.append(name)
        except Exception as exc:
            logger.warning("Deterministic entity union failed: %s", exc)
    return merged


_CAPITALIZED_PHRASE_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")
# ALL-CAPS names ("NVIDIA", "AMD") never match the Title-case phrase above,
# yet they are exactly what multi-company comparisons use. Without this the
# fallback silently drops them and only one entity reaches Yahoo (the live
# single-series failure mode).
_ALLCAPS_TOKEN_RE = re.compile(r"\b([A-Z]{2,}(?:\s+[A-Z]{2,})?)\b")


def _fallback_entities_from_text(text: str) -> list[str]:
    """Deterministic entity candidates when the query rewriter yields none
    (LLM outage, parse failure): alias mentions first, then capitalized
    phrases minus stopwords. Candidates are NOT trusted — each still goes
    through Yahoo symbol search, which returns None for anything untradable,
    so a false positive costs one cheap HTTP call at most. Without this, a
    rewrite failure silently disables all market/fundamentals evidence even
    when the query names "Tesla" and "Reliance" outright (seen live)."""
    found: list[str] = []
    seen: set[str] = set()
    for name in STOCK_ALIASES:
        if re.search(rf"\b{re.escape(name)}\b", text or "", re.IGNORECASE):
            canonical = name
            # Normalise alias display ("Nvidia" -> "NVIDIA").
            if name.lower() == "nvidia":
                canonical = "NVIDIA"
            if name.lower() in ("amd", "advanced micro devices"):
                canonical = "AMD"
            if canonical.lower() not in seen:
                found.append(canonical)
                seen.add(canonical.lower())
            if len(found) >= MAX_MARKET_ENTITIES:
                return found
    # ALL-CAPS pass first so "Compare NVIDIA and AMD" keeps both even
    # though neither matches the Title-case pattern below.
    for match in _ALLCAPS_TOKEN_RE.finditer(text or ""):
        candidate = " ".join(match.group(1).split())
        key = candidate.lower()
        if key in seen or key in _FALLBACK_ENTITY_STOPWORDS:
            continue
        # Skip single letters / generic shouting; keep plausible tickers/names.
        if len(candidate) < 2:
            continue
        seen.add(key)
        found.append(candidate)
        if len(found) >= MAX_MARKET_ENTITIES:
            return found
    for match in _CAPITALIZED_PHRASE_RE.finditer(text or ""):
        phrase = " ".join(match.group(1).split())
        words = phrase.split()
        # Strip leading stopwords ("Compare Tesla" -> "Tesla").
        while words and words[0].lower() in _FALLBACK_ENTITY_STOPWORDS:
            words.pop(0)
        if not words:
            continue
        candidate = " ".join(words)
        key = candidate.lower()
        if key in seen or key in _FALLBACK_ENTITY_STOPWORDS:
            continue
        seen.add(key)
        found.append(candidate)
        if len(found) >= MAX_MARKET_ENTITIES:
            break
    return found


@dataclass
class WebSearchResult:
    context: list[str]
    sources: list[dict[str, Any]]
    market_data: list[dict[str, object]]
    fundamentals: list[dict[str, Any]] = field(default_factory=list)
    macro_data: list[dict[str, object]] = field(default_factory=list)
    # Multi-year evidence for historical comparisons. market_data stays the
    # 1-month snapshot series (short-term only); price_history carries the
    # multi-year closes and financial_history the annual revenue/profit
    # series. Keeping them separate is what makes "1 month cannot satisfy
    # 3 years" checkable instead of silently charting the wrong window.
    price_history: list[dict[str, object]] = field(default_factory=list)
    financial_history: list[dict[str, Any]] = field(default_factory=list)
    # Machine-written research trace (which entities were required, what the
    # second pass did). Shown in logs/thinking, never narrated as fact.
    research_notes: list[str] = field(default_factory=list)


class _DuckDuckGoParser(HTMLParser):
    """Collects (text, url) pairs, merging each result's title with the
    snippet that follows it ("title — snippet") so every snippet carries its
    page URL. Pairs keep context[i] <-> sources[i] aligned 1:1 downstream."""

    def __init__(self) -> None:
        super().__init__()
        self.pairs: list[tuple[str, str]] = []
        self._capture = False
        self._buffer: list[str] = []
        self._kind = ""
        self._capture_tag = ""
        self._source_url = ""
        self._pending: Optional[tuple[str, str]] = None

    def _flush_pending(self) -> None:
        if self._pending is not None:
            self.pairs.append(self._pending)
            self._pending = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        classes = dict(attrs).get("class", "") or ""
        if tag == "a" and "result-link" in classes:
            self._capture = True
            self._buffer = []
            self._kind = "title"
            self._capture_tag = tag
            self._source_url = dict(attrs).get("href", "") or ""
        elif "result__snippet" in classes:
            self._capture = True
            self._buffer = []
            self._kind = "snippet"
            self._capture_tag = tag
            # Snippets are often bare divs; fall back to the block's title URL.
            self._source_url = dict(attrs).get("href", "") or (
                self._pending[1] if self._pending else ""
            )

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag == self._capture_tag:
            text = " ".join("".join(self._buffer).split())
            if text:
                text = html.unescape(text)
                if self._kind == "title":
                    self._flush_pending()
                    self._pending = (text, self._source_url)
                else:
                    if self._pending is not None:
                        title, url = self._pending
                        self._pending = None
                        self.pairs.append(
                            (f"{title} — {text}", self._source_url or url)
                        )
                    else:
                        self.pairs.append((text, self._source_url))
            self._capture = False
            self._kind = ""
            self._capture_tag = ""
            self._source_url = ""

    def close(self) -> None:
        super().close()
        self._flush_pending()


def _dedupe_texts(items: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in items:
        key = " ".join(item.lower().split())
        if key and key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _normalize_published_date(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Tavily returns ISO datetimes; the model only needs the day.
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    return text[:10] or None


async def _resolve_symbol(
    client: httpx.AsyncClient, entity: str
) -> Optional[str]:
    """Resolve any entity name to a tradable symbol: alias fast path, then
    Yahoo's symbol search. None when nothing tradable matches."""
    for name, symbol in STOCK_ALIASES.items():
        if entity.lower() == name.lower() or re.search(
            rf"\b{name}\b", entity, re.IGNORECASE
        ):
            return symbol
    try:
        response = await client.get(
            f"https://query2.finance.yahoo.com/v1/finance/search?q={quote_plus(entity)}",
            headers={**_HEADERS, "Accept": "application/json"},
        )
        response.raise_for_status()
        for quote in response.json().get("quotes", []):
            if quote.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND") and quote.get(
                "symbol"
            ):
                return str(quote["symbol"])
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("Symbol search failed for %s: %s", entity, exc)
    return None


async def _fetch_market_series(
    client: httpx.AsyncClient, entity: str, symbol: str
) -> Optional[tuple[str, dict, dict]]:
    """One-month daily closes for a symbol. Returns (text, series, source)."""
    try:
        response = await client.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d",
            headers={**_HEADERS, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        chart = payload["chart"]["result"][0]
        closes = [
            value
            for value in chart["indicators"]["quote"][0]["close"]
            if value is not None
        ]
        if len(closes) < 2 or closes[0] == 0:
            return None
        change = (closes[-1] - closes[0]) / closes[0] * 100
        currency = chart["meta"].get("currency", "")
        timestamps = chart.get("timestamp", [])
        labels = [
            datetime.fromtimestamp(timestamp, timezone.utc).strftime("%b %d")
            for timestamp in timestamps[-len(closes):]
        ]
        text = (
            f"{entity} ({symbol}) one-month market data: "
            f"first close {closes[0]:.2f} {currency}, "
            f"latest close {closes[-1]:.2f} {currency}, "
            f"change {change:+.2f}% (computed from Yahoo Finance daily closes)."
        )
        series = {
            "entity": entity,
            "symbol": symbol,
            "currency": currency,
            "labels": labels,
            "values": closes,
        }
        source = {
            "title": f"Yahoo Finance: {entity} ({symbol}) historical data",
            "url": f"https://finance.yahoo.com/quote/{symbol}/history/",
            "provider": "Yahoo Finance",
        }
        return text, series, source
    except (KeyError, IndexError, JSONDecodeError, ValueError, httpx.HTTPError) as exc:
        logger.warning("Market data lookup failed for %s: %s", entity, exc)
        return None


def _pick_number(*candidates: Any) -> Optional[float]:
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, (int, float)):
            return float(candidate)
        if isinstance(candidate, dict):
            for key in ("raw", "fmt", "value"):
                picked = _pick_number(candidate.get(key))
                if picked is not None:
                    return picked
        if isinstance(candidate, str):
            cleaned = candidate.replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                continue
    return None


async def _get_yahoo_crumb(
    client: httpx.AsyncClient, *, force_refresh: bool = False
) -> tuple[Optional[str], str]:
    """Mint (crumb, cookie-header) for Yahoo's authed endpoints.

    quoteSummary and v7/quote return 401 "Invalid Crumb" without a crumb
    minted against the current session cookie. Cached process-local for
    _YAHOO_CRUMB_TTL_SECONDS; (None, "") on any failure so callers skip
    gracefully instead of raising.
    """
    now = time.monotonic()
    if (
        not force_refresh
        and _yahoo_crumb["crumb"]
        and now - _yahoo_crumb["at"] < _YAHOO_CRUMB_TTL_SECONDS
    ):
        return _yahoo_crumb["crumb"], _yahoo_crumb["cookie"]
    try:
        await client.get("https://fc.yahoo.com", headers=_HEADERS)
        cookie = "; ".join(
            f"{name}={value}"
            for name, value in client.cookies.items()
        )
        response = await client.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers={**_HEADERS, "Cookie": cookie} if cookie else _HEADERS,
        )
        response.raise_for_status()
        crumb = response.text.strip()
        if not crumb:
            return None, ""
        _yahoo_crumb["crumb"] = crumb
        _yahoo_crumb["cookie"] = cookie
        _yahoo_crumb["at"] = now
        return crumb, cookie
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Yahoo crumb handshake failed: %s", exc)
        return None, ""


async def _fetch_fundamentals(
    client: httpx.AsyncClient, entity: str, symbol: str
) -> Optional[tuple[str, dict, dict]]:
    """Structured fundamentals via Yahoo quoteSummary: market cap, trailing
    P/E, total revenue. Genuine numbers with entity attached - never
    regex-scraped prose. Sends the crumb+cookie pair quoteSummary requires;
    one fresh-crumb retry on 401, then graceful None - never raises."""
    modules = "price,summaryDetail,defaultKeyStatistics,financialData"
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    crumb, cookie = await _get_yahoo_crumb(client)
    if crumb is None:
        logger.info("Fundamentals skipped for %s: no Yahoo crumb.", symbol)
        return None
    for attempt in range(2):
        try:
            headers = {**_HEADERS, "Accept": "application/json"}
            if cookie:
                headers["Cookie"] = cookie
            response = await client.get(
                url, params={"modules": modules, "crumb": crumb}, headers=headers
            )
            if response.status_code == 401 and attempt == 0:
                crumb, cookie = await _get_yahoo_crumb(client, force_refresh=True)
                if crumb is None:
                    return None
                continue
            response.raise_for_status()
            return _parse_fundamentals(response.json(), entity, symbol)
        except httpx.HTTPError as exc:
            logger.warning("Fundamentals lookup failed for %s: %s", entity, exc)
            return None
    return None


def _parse_fundamentals(
    payload: Any, entity: str, symbol: str
) -> Optional[tuple[str, dict, dict]]:
    """Parse a quoteSummary payload into (text, fundamentals, source)."""
    try:
        result = payload["quoteSummary"]["result"][0]
        price = result.get("price", {})
        stats = result.get("defaultKeyStatistics", {})
        summary = result.get("summaryDetail", {})
        financials = result.get("financialData", {})
        currency = str(price.get("currency", {}).get("raw", "") or "")
        market_cap = _pick_number(
            stats.get("marketCap"), price.get("marketCap"),
            summary.get("marketCap"),
        )
        pe_ratio = _pick_number(
            stats.get("trailingPE"), summary.get("trailingPE"),
            stats.get("forwardPE"), summary.get("forwardPE"),
        )
        revenue = _pick_number(financials.get("totalRevenue"))
        if market_cap is None and pe_ratio is None and revenue is None:
            return None
        bits = []
        if market_cap is not None:
            bits.append(f"market cap {market_cap:,.0f} {currency}".strip())
        if pe_ratio is not None:
            bits.append(f"trailing P/E {pe_ratio:.2f}")
        if revenue is not None:
            bits.append(f"revenue {revenue:,.0f} {currency}".strip())
        text = (
            f"{entity} ({symbol}) fundamentals ({', '.join(bits)}) "
            f"(Yahoo Finance quoteSummary)."
        )
        fundamentals = {
            "entity": entity,
            "symbol": symbol,
            "currency": currency,
            "market_cap": market_cap,
            "pe_ratio": pe_ratio,
            "revenue": revenue,
        }
        source = {
            "title": f"Yahoo Finance: {entity} ({symbol}) fundamentals",
            "url": f"https://finance.yahoo.com/quote/{symbol}/",
            "provider": "Yahoo Finance",
        }
        return text, fundamentals, source
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Fundamentals parse failed for %s: %s", entity, exc)
        return None


async def _fetch_market_history(
    client: httpx.AsyncClient,
    entity: str,
    symbol: str,
    years: int = 3,
) -> Optional[tuple[str, dict, dict]]:
    """Multi-year weekly closes for a symbol (the HISTORICAL price adapter).

    This is the ONLY Yahoo price adapter that can answer "last N years":
    ``_fetch_market_series`` is hardcoded to ``range=1mo`` (short-term only)
    and ``_fetch_fundamentals``/quoteSummary is a current snapshot with no
    dates at all. Callers must treat a missing history as insufficient
    evidence -- never fall back to charting the 1-month series as if it
    covered 3 years. Returns (text, series, source); never raises.
    """
    years = max(1, min(int(years or 3), 10))
    try:
        response = await client.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?range={years}y&interval=1wk",
            headers={**_HEADERS, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        chart = payload["chart"]["result"][0]
        closes = [
            value
            for value in chart["indicators"]["quote"][0]["close"]
            if value is not None
        ]
        if len(closes) < 2 or closes[0] == 0:
            return None
        change = (closes[-1] - closes[0]) / closes[0] * 100
        currency = chart["meta"].get("currency", "")
        timestamps = chart.get("timestamp", [])
        labels = [
            datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
            for timestamp in timestamps[-len(closes):]
        ]
        period_start = labels[0] if labels else ""
        period_end = labels[-1] if labels else ""
        text = (
            f"{entity} ({symbol}) {years}-year market history: "
            f"first close {closes[0]:.2f} {currency} on {period_start}, "
            f"latest close {closes[-1]:.2f} {currency} on {period_end}, "
            f"change {change:+.2f}% (computed from Yahoo Finance weekly closes)."
        )
        series = {
            "entity": entity,
            "symbol": symbol,
            "currency": currency,
            "labels": labels,
            "values": closes,
            "range": f"{years}y",
            "interval": "1wk",
            "frequency": "weekly",
            "period_start": period_start,
            "period_end": period_end,
            "is_historical": True,
            "metric": "close",
        }
        source = {
            "title": f"Yahoo Finance: {entity} ({symbol}) {years}Y history",
            "url": f"https://finance.yahoo.com/quote/{symbol}/history/",
            "provider": "Yahoo Finance",
        }
        return text, series, source
    except (KeyError, IndexError, JSONDecodeError, ValueError, httpx.HTTPError) as exc:
        logger.warning("Market history lookup failed for %s: %s", entity, exc)
        return None


async def _fetch_financial_history(
    client: httpx.AsyncClient,
    entity: str,
    symbol: str,
    years: int = 3,
) -> Optional[tuple[str, dict, dict]]:
    """Annual revenue + net-income history for a symbol (HISTORICAL adapter).

    quoteSummary cannot do this: it returns ONE current totalRevenue figure
    with no dates. This adapter reads Yahoo's fundamentals timeseries
    (annualTotalRevenue / annualNetIncome) so revenue GROWTH and
    profitability are computed over the same multi-year window for both
    companies. Graceful None when the endpoint is unavailable -- never raises,
    never synthesises numbers.
    """
    years = max(1, min(int(years or 3), 10))
    try:
        url = (
            "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
            f"{symbol}?type=annualTotalRevenue,annualNetIncome&merge=false&period1=0&period2=9999999999"
        )
        # The timeseries endpoint is flaky without a session crumb (401s
        # seen live, which silently dropped ALL revenue/profit evidence).
        # Mint one when available; proceed without it when not.
        crumb, cookie = await _get_yahoo_crumb(client)
        headers = {**_HEADERS, "Accept": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        params: dict[str, Any] = {}
        if crumb:
            params["crumb"] = crumb
        response = await client.get(url, params=params or None, headers=headers)
        if response.status_code == 401 and crumb is not None:
            crumb, cookie = await _get_yahoo_crumb(client, force_refresh=True)
            headers = {**_HEADERS, "Accept": "application/json"}
            if cookie:
                headers["Cookie"] = cookie
            params = {"crumb": crumb} if crumb else {}
            response = await client.get(url, params=params or None, headers=headers)
        response.raise_for_status()
        payload = response.json()
        series_list = payload.get("timeseries", {}).get("result", [])
        by_type: dict[str, list[tuple[str, float]]] = {}
        for entry in series_list:
            if not isinstance(entry, dict):
                continue
            # Meta "type" may be a string or a list of strings.
            raw_meta_type = (entry.get("meta", {}) or {}).get("type", "")
            if isinstance(raw_meta_type, list):
                meta_types = [str(t) for t in raw_meta_type if t]
            elif raw_meta_type:
                meta_types = [str(raw_meta_type)]
            else:
                meta_types = []
            # Yahoo layout: one observation list per declared type key,
            # each item {"asOfDate": ..., "reportedValue": {"raw": ...}}.
            # Fall back to the first list-of-dicts key when the layout shifts.
            for meta_type in meta_types or ["__scan__"]:
                values = entry.get(meta_type, []) if meta_type != "__scan__" else []
                if not values:
                    for key, val in entry.items():
                        if key in ("meta", "timestamp"):
                            continue
                        if isinstance(val, list) and val and isinstance(val[0], dict):
                            values = val
                            if not meta_type or meta_type == "__scan__":
                                meta_type = key
                            break
                points: list[tuple[str, float]] = []
                for obs in values or []:
                    if not isinstance(obs, dict):
                        continue
                    as_of = str(obs.get("asOfDate", "") or "")[:10]
                    raw = (obs.get("reportedValue", {}) or {}).get("raw", None)
                    try:
                        number = float(raw) if raw is not None else None
                    except (TypeError, ValueError):
                        number = None
                    if as_of and number is not None:
                        points.append((as_of, number))
                if not points:
                    continue
                points.sort(key=lambda p: p[0])
                # Keep the trailing `years + 1` annual points (need start AND end).
                key = meta_type or "unknown"
                merged = sorted(by_type.get(key, []) + points, key=lambda p: p[0])
                # Dedupe by date, last write wins, then keep the trailing window.
                deduped: dict[str, float] = {}
                for stamp, number in merged:
                    deduped[stamp] = number
                by_type[key] = sorted(deduped.items())[-(years + 1):]
        revenue_pts = (
            by_type.get("annualTotalRevenue")
            or by_type.get("totalRevenue")
            or []
        )
        income_pts = by_type.get("annualNetIncome") or by_type.get("netIncome") or []
        # Fallback key scan when meta types differ across regions.
        if not revenue_pts or not income_pts:
            for key, pts in by_type.items():
                lowered = key.lower()
                if not revenue_pts and "revenue" in lowered:
                    revenue_pts = pts
                if not income_pts and ("income" in lowered or "earning" in lowered):
                    income_pts = pts
        if len(revenue_pts) < 2 and len(income_pts) < 2:
            return None
        bits: list[str] = []
        # H5: explicit reporting currency on every financial evidence object.
        # The timeseries endpoint carries no currency field, so infer from
        # the Yahoo symbol suffix (documented heuristic: US-listed -> USD,
        # .NS -> INR, .T -> JPY, ...). Explicit-inferred beats silent-unknown;
        # callers still treat mixed known codes as incompatible absolutes.
        try:
            from app.services.data.comparison import (
                _infer_currency_for_symbol as _infer_ccy,
            )

            inferred_ccy = _infer_ccy(symbol)
        except Exception:
            inferred_ccy = None
        payload_out: dict[str, Any] = {
            "entity": entity,
            "symbol": symbol,
            "frequency": "annual",
            "is_historical": True,
            "currency": inferred_ccy,
            "currency_inferred": True if inferred_ccy else False,
        }
        if len(revenue_pts) >= 2:
            start_rev, end_rev = revenue_pts[0][1], revenue_pts[-1][1]
            growth = (end_rev - start_rev) / abs(start_rev) * 100 if start_rev else None
            payload_out["revenue"] = {
                "labels": [p[0] for p in revenue_pts],
                "values": [p[1] for p in revenue_pts],
                "period_start": revenue_pts[0][0],
                "period_end": revenue_pts[-1][0],
                "metric": "annualTotalRevenue",
                "definition": "annualTotalRevenue",
                "unit": inferred_ccy or "currency",
                "currency": inferred_ccy,
                "scale": 1.0,
                "frequency": "annual",
                "source_type": "yahoo",
            }
            if growth is not None:
                bits.append(f"revenue {start_rev:,.0f} -> {end_rev:,.0f} ({growth:+.2f}%)")
        if len(income_pts) >= 2:
            payload_out["net_income"] = {
                "labels": [p[0] for p in income_pts],
                "values": [p[1] for p in income_pts],
                "period_start": income_pts[0][0],
                "period_end": income_pts[-1][0],
                "metric": "annualNetIncome",
                "definition": "annualNetIncome",
                "unit": inferred_ccy or "currency",
                "currency": inferred_ccy,
                "scale": 1.0,
                "frequency": "annual",
                "source_type": "yahoo",
            }
            bits.append(f"net income {income_pts[0][1]:,.0f} -> {income_pts[-1][1]:,.0f}")
        if not bits:
            return None
        text = (
            f"{entity} ({symbol}) {years}-year financial history: "
            + "; ".join(bits)
            + " (Yahoo Finance fundamentals timeseries, annual)."
        )
        source = {
            "title": f"Yahoo Finance: {entity} ({symbol}) annual financials",
            "url": f"https://finance.yahoo.com/quote/{symbol}/financials/",
            "provider": "Yahoo Finance",
        }
        return text, payload_out, source
    except (KeyError, IndexError, JSONDecodeError, ValueError, httpx.HTTPError) as exc:
        logger.warning("Financial history lookup failed for %s: %s", entity, exc)
        return None


def _find_urls(text: str) -> list[str]:
    """URLs mentioned verbatim in the query (Tavily Extract candidates)."""
    found: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?")
        if url and url not in found:
            found.append(url)
    return found[:MAX_EXTRACT_URLS]


async def _tavily_extract(
    client: httpx.AsyncClient, url: str, query: str, settings
) -> Optional[tuple[str, dict]]:
    """Full clean content for one URL via Tavily Extract (basic depth).

    For follow-ups clearly about a cited article ("that article says...").
    Gated to URLs in the raw query, one per request; without a Tavily key
    it skips. Truncated to EXTRACT_MAX_CHARS. Never raises."""
    if not getattr(settings, "WEB_SEARCH_API_KEY", None):
        return None
    try:
        response = await client.post(
            "https://api.tavily.com/extract",
            json={
                "api_key": settings.WEB_SEARCH_API_KEY,
                "urls": url,
                "query": (query or "")[:500],
                "extract_depth": "basic",
            },
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        content = str(results[0].get("raw_content", "") or "").strip()
        if not content:
            return None
        trimmed = content[:EXTRACT_MAX_CHARS]
        text = f"Full content from {url}: {trimmed} (Tavily Extract.)"
        source = {
            "title": f"Extracted: {url[:80]}",
            "url": url,
            "provider": "Tavily Extract",
        }
        return text, source
    except (KeyError, IndexError, JSONDecodeError, ValueError, httpx.HTTPError) as exc:
        logger.warning("Tavily extract failed for %s: %s", url, exc)
        return None


async def _fetch_wikipedia(
    client: httpx.AsyncClient, entity: str
) -> Optional[tuple[str, dict]]:
    """Canonical grounding for 'who/what is X' via the Wikipedia REST
    summary API. Free, structured, no key. Disambiguation pages are skipped
    (return None) rather than guessed. Never raises."""
    try:
        response = await client.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/"
            f"{quote_plus(entity)}",
            headers={**_HEADERS, "Accept": "application/json"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if payload.get("type") == "disambiguation":
            logger.info("Wikipedia disambiguation for %r, skipping.", entity)
            return None
        title = str(payload.get("title", entity) or entity)
        extract = str(payload.get("extract", "") or "").strip()
        if not extract:
            return None
        page_url = (
            (payload.get("content_urls") or {})
            .get("desktop", {})
            .get("page", "")
        ) or f"https://en.wikipedia.org/wiki/{quote_plus(entity)}"
        trimmed = extract[:WIKI_SUMMARY_MAX_CHARS]
        text = f"{title}: {trimmed} (Wikipedia summary.)"
        source = {
            "title": f"Wikipedia: {title}",
            "url": str(page_url),
            "provider": "Wikipedia",
        }
        return text, source
    except (KeyError, IndexError, JSONDecodeError, ValueError, httpx.HTTPError) as exc:
        logger.warning("Wikipedia lookup failed for %s: %s", entity, exc)
        return None


def _detect_fred_series(query_text: str) -> list[tuple[str, str]]:
    """Map macro intent to FRED series ids (generic topics, fixed ids)."""
    found: list[tuple[str, str]] = []
    for pattern, series_id, label in FRED_SERIES_MAP:
        if pattern.search(query_text or ""):
            found.append((label, series_id))
    return found[:2]


async def _fetch_fred_series(
    client: httpx.AsyncClient, label: str, series_id: str, settings
) -> Optional[tuple[str, dict, dict]]:
    """Structured macro series via FRED observations (last 12 points).
    Needs FRED_API_KEY (free); without it, skips gracefully - never raises."""
    api_key = getattr(settings, "FRED_API_KEY", None)
    if not api_key:
        logger.info("FRED skipped for %s: no FRED_API_KEY configured.", series_id)
        return None
    try:
        response = await client.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 12,
            },
            headers=_HEADERS,
        )
        response.raise_for_status()
        observations = response.json().get("observations", [])
        points = [
            (obs.get("date", ""), obs.get("value", ""))
            for obs in observations
            if obs.get("value") not in (None, "", ".")
        ]
        if len(points) < 2:
            return None
        points = list(reversed(points))
        labels = [date for date, _ in points]
        values = [float(value) for _, value in points]
        text = (
            f"{label} ({series_id}) macro data: latest {values[-1]} on "
            f"{labels[-1]}, prior {values[0]} on {labels[0]} "
            f"(FRED series observations)."
        )
        series = {
            "entity": label,
            "symbol": series_id,
            "currency": "",
            "labels": labels,
            "values": values,
        }
        source = {
            "title": f"FRED: {label} ({series_id})",
            "url": f"https://fred.stlouisfed.org/series/{series_id}",
            "provider": "FRED",
        }
        return text, series, source
    except (KeyError, IndexError, JSONDecodeError, ValueError, httpx.HTTPError) as exc:
        logger.warning("FRED lookup failed for %s: %s", series_id, exc)
        return None


async def _tavily_search(
    client: httpx.AsyncClient,
    query_item: str,
    settings,
    time_sensitive: bool = False,
) -> list[tuple]:
    """Returns (text, url, provider, published_date, score) tuples, answer first.

    Time-sensitive queries route to topic="news" + time_range="week" so the
    model sees fresh results; everything else uses topic="general".
    published_date/score come straight from Tavily (no guessing).
    """
    body: dict[str, Any] = {
        "api_key": settings.WEB_SEARCH_API_KEY,
        "query": query_item,
        "search_depth": "basic",
        "max_results": settings.WEB_SEARCH_MAX_RESULTS,
        "include_answer": True,
    }
    if time_sensitive:
        body["topic"] = "news"
        body["time_range"] = "week"
    response = await client.post(
        "https://api.tavily.com/search",
        json=body,
    )
    response.raise_for_status()
    payload = response.json()
    triples: list[tuple] = []
    if payload.get("answer"):
        triples.append((str(payload["answer"]), "", "Tavily", None, None))
    for item in payload.get("results", []):
        if not item.get("content"):
            continue
        triples.append(
            (
                f"{item.get('title', '')}: {item.get('content', '')}",
                str(item.get("url", "")),
                "Tavily",
                _normalize_published_date(item.get("published_date")),
                item.get("score"),
            )
        )
    return triples


async def _ddg_search(
    client: httpx.AsyncClient, query_item: str, settings
) -> list[tuple]:
    """Returns (text, url, provider, published_date, score) tuples from merged
    title/snippet pairs. DDG carries no dates/scores (None, None)."""
    response = await client.get(
        f"https://html.duckduckgo.com/html/?q={quote_plus(query_item)}",
        headers=_HEADERS,
    )
    # DDG answers bot-suspect traffic with 202 + a JS challenge page.
    # raise_for_status() passes on 202, and the parser would feed on challenge
    # HTML and return zero pairs — a silent empty-evidence failure (seen live:
    # "202 Accepted" in logs, no snippets, honest-but-wrong "could not
    # locate" downstream). Treat any non-200 as a provider failure so the
    # merge path logs it and the other provider carries the request.
    if response.status_code != 200:
        raise RuntimeError(
            f"DuckDuckGo returned HTTP {response.status_code} "
            f"(expected 200; challenge/captcha pages carry no results)."
        )
    response.raise_for_status()
    parser = _DuckDuckGoParser()
    parser.feed(response.text)
    parser.close()
    return [
        (text, url, "DuckDuckGo", None, None)
        for text, url in parser.pairs[: settings.WEB_SEARCH_MAX_RESULTS]
    ]


def _coerce_triple(triple: tuple) -> tuple[str, str, str, Optional[str], Any]:
    """Accept legacy 3-tuples (tests/fakes) and new 5-tuples uniformly."""
    if len(triple) >= 5:
        text, url, provider, published, score = triple[:5]
        return str(text), str(url or ""), str(provider), published, score
    text, url, provider = triple[:3]
    return str(text), str(url or ""), str(provider), None, None


# ---------------------------------------------------------------------------
# Second-pass financial research: when a historical comparison still lacks
# annual revenue / net-income history after the Yahoo pass (endpoint down,
# symbol unresolvable, timeseries sparse), research each missing company
# explicitly via targeted web queries and deterministically extract
# per-fiscal-year figures from the snippets. Missing financial evidence
# must trigger MORE research -- never a clarification asking the user to
# supply public company statistics.
# ---------------------------------------------------------------------------

# Cap on per-entity second-pass queries (latency/cost bound).
MAX_FINANCIAL_RESEARCH_ENTITIES = 3

_FIN_MONEY_RE = re.compile(
    r"(US\$|\$|€|₹|£|¥|CN¥|RMB)?\s?"
    r"(\d[\d,]*(?:\.\d+)?)\s?"
    r"(trillion|billion|million|thousand|tn|bn|mn|t|b|m|k)?"
    r"(?:\s?(USD|CNY|RMB|JPY|yuan|yen|dollars?))?",
    re.IGNORECASE,
)
_FIN_YEAR_RE = re.compile(
    r"\b(FY\s?20\d{2}|F\.Y\.\s?20\d{2}|fiscal\s+(?:year\s+)?20\d{2}|"
    r"fiscal\s+20\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
_FIN_REVENUE_RE = re.compile(
    r"\b(total\s+)?(revenue|revenues|sales|turnover|net\s+sales)\b",
    re.IGNORECASE,
)
_FIN_INCOME_RE = re.compile(
    r"\b(net\s*(income|earnings|profit|loss(es)?)|net\s+income\s+attributable)\b",
    re.IGNORECASE,
)
_FIN_SCALE = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}


def _normalize_fin_year(raw: str) -> str:
    """Normalize a year mention to a stable fiscal label, preserving FY-ness.

    "FY2023" / "fiscal 2023" -> "FY2023"; bare "2023" -> "2023". Labels are
    preserved verbatim-ish so differing fiscal years stay visible.
    """
    text = " ".join((raw or "").split())
    year_match = re.search(r"20\d{2}", text)
    year = year_match.group(0) if year_match else text
    # NOTE: no trailing \b after Y -- "FY2023" has no boundary between Y
    # and the digits, and \bF\.?Y\.?\b would miss it (live bug).
    if re.search(r"F\.?Y\.?|fiscal", text, re.IGNORECASE):
        return f"FY{year}" if year != text else text
    return year


def _extract_financials_from_snippets(
    entity: str,
    triples: list[tuple],
    years: int = 3,
) -> Optional[tuple[str, dict, dict]]:
    """Deterministically extract annual revenue + net-income series for one
    entity from web-snippet triples (text, url, provider, published, score).

    A figure is used only when its sentence mentions the entity AND a
    revenue/income cue AND a year label -- unattributed or undated money
    never enters. Needs >= 2 distinct years per metric to emit a series
    (growth is undefined otherwise). Returns (text, payload, source) shaped
    exactly like the Yahoo financial_history adapter; None when the
    snippets cannot support a series. Never raises, never invents.
    """
    try:
        collected: dict[str, dict[str, tuple[str, float, str]]] = {
            "revenue": {}, "net_income": {},
        }
        first_url: dict[str, str] = {}
        _PRICE_GUARD_RE = re.compile(
            r"\b(stock|stocks|share|shares|closing?|stock\s*price)\b",
            re.IGNORECASE,
        )
        for triple in triples or []:
            text, url, _provider, _published, _score = _coerce_triple(triple)
            if not text or entity.lower() not in text.lower():
                continue
            first_url.setdefault(entity, url)
            # Split on newlines/semicolons/pipes and real sentence
            # boundaries -- never inside decimals like "96.8".
            for sentence in re.split(
                r"\n+|;+|\|+|(?<=[.!?])\s+(?=[A-Z0-9(])", text
            ):
                if entity.lower() not in sentence.lower():
                    continue
                year_hits = [
                    (match.start(), _normalize_fin_year(match.group(0)))
                    for match in _FIN_YEAR_RE.finditer(sentence)
                ]
                if not year_hits:
                    continue
                rev_hits = [
                    match.start() for match in _FIN_REVENUE_RE.finditer(sentence)
                ]
                inc_hits = [
                    match.start() for match in _FIN_INCOME_RE.finditer(sentence)
                ]
                if not rev_hits and not inc_hits:
                    continue
                for money in _FIN_MONEY_RE.finditer(sentence):
                    digits = (money.group(2) or "").replace(",", "").rstrip(",")
                    try:
                        amount = float(digits)
                    except (TypeError, ValueError):
                        continue
                    # Skip bare small numbers and bare years (dates, counts).
                    scale_word = (money.group(3) or "").lower()
                    symbol = money.group(1) or ""
                    currency_word = money.group(4) or ""
                    if not scale_word and not symbol and not currency_word:
                        if amount < 1000 or (1900 <= amount <= 2100):
                            continue
                    # Skip stock-price figures ("... stock price hit $248
                    # in 2023" must not become revenue): a price-word just
                    # before the figure disqualifies it.
                    window = sentence[max(0, money.start() - 20):money.start()]
                    if _PRICE_GUARD_RE.search(window):
                        continue
                    # Nearest cue wins (a sentence may carry revenue AND
                    # income). Year attribution PREFERS the year stated AFTER
                    # the figure ("$96.8 billion in FY2023"): financial prose
                    # puts the period after the amount, and pure-nearest
                    # attribution ties/misattributes ("... in FY2023, up from
                    # $81.5B in FY2022" bound BOTH figures to FY2023, which
                    # collapsed 2-year evidence to 1 year and silently killed
                    # the series). Only when no year follows does the nearest
                    # preceding year win.
                    pos = money.start()
                    nearest_rev = (
                        min(abs(pos - hit) for hit in rev_hits)
                        if rev_hits else float("inf")
                    )
                    nearest_inc = (
                        min(abs(pos - hit) for hit in inc_hits)
                        if inc_hits else float("inf")
                    )
                    cue = (
                        "net_income"
                        if nearest_inc <= nearest_rev
                        else "revenue"
                    )
                    following = [hit for hit in year_hits if hit[0] >= money.end()]
                    if following:
                        year_label = min(following, key=lambda hit: hit[0] - pos)[1]
                    else:
                        year_label = min(
                            year_hits, key=lambda hit: abs(hit[0] - pos)
                        )[1]
                    try:
                        from app.services.data.comparison import (
                            normalize_currency as _normalize_currency,
                        )
                        figure_currency = _normalize_currency(symbol, currency_word)
                    except Exception:
                        figure_currency = None
                    bucket = collected[cue]
                    # First-seen wins per (metric, year): deterministic, and
                    # the lead snippet is usually the most relevant result.
                    if year_label not in bucket:
                        bucket[year_label] = (year_label, amount * _FIN_SCALE.get(scale_word, 1), sentence.strip()[:200], figure_currency)
        # Keep only the trailing window of years (latest completed periods).
        series: dict[str, list[tuple[str, float]]] = {}
        series_currency: dict[str, Optional[str]] = {}
        for cue, bucket in collected.items():
            ordered = sorted(bucket.values(), key=lambda item: item[0])
            windowed = ordered[-(years + 1):]
            series[cue] = [(label, value) for label, value, _ctx, _cur in windowed]
            # Explicit currency when every point in-window agrees; else
            # explicitly unknown (None) -- never a silent default.
            window_currencies = {cur for _, _, _, cur in windowed if cur}
            series_currency[cue] = (
                next(iter(window_currencies)) if len(window_currencies) == 1 else None
            )
        revenue_pts = series.get("revenue", [])
        income_pts = series.get("net_income", [])
        if len(revenue_pts) < 2 and len(income_pts) < 2:
            return None
        payload_out: dict[str, Any] = {
            "entity": entity,
            "symbol": "",
            "frequency": "annual",
            "is_historical": True,
            "provenance": "web_snippets",
        }
        bits: list[str] = []
        if len(revenue_pts) >= 2:
            payload_out["revenue"] = {
                "labels": [label for label, _ in revenue_pts],
                "values": [value for _, value in revenue_pts],
                "period_start": revenue_pts[0][0],
                "period_end": revenue_pts[-1][0],
                "metric": "annualTotalRevenue",
                "unit": "currency",
                # Explicit ISO code when every in-window figure agreed on
                # one; None = explicitly unknown (never a silent default).
                "currency": series_currency.get("revenue"),
                "frequency": "annual",
                "definition": "annualTotalRevenue",
                "source_type": "web_snippets",
            }
            growth = (
                (revenue_pts[-1][1] - revenue_pts[0][1]) / abs(revenue_pts[0][1]) * 100
                if revenue_pts[0][1] else None
            )
            span = f"{revenue_pts[0][0]} -> {revenue_pts[-1][0]}"
            bits.append(
                f"revenue {revenue_pts[0][1]:,.0f} -> {revenue_pts[-1][1]:,.0f} ({span})"
                + (f" ({growth:+.2f}%)" if growth is not None else "")
            )
        if len(income_pts) >= 2:
            payload_out["net_income"] = {
                "labels": [label for label, _ in income_pts],
                "values": [value for _, value in income_pts],
                "period_start": income_pts[0][0],
                "period_end": income_pts[-1][0],
                "metric": "annualNetIncome",
                "unit": "currency",
                "currency": series_currency.get("net_income"),
                "frequency": "annual",
                "definition": "annualNetIncome",
                "source_type": "web_snippets",
            }
            bits.append(
                f"net income {income_pts[0][1]:,.0f} -> {income_pts[-1][1]:,.0f} "
                f"({income_pts[0][0]} -> {income_pts[-1][0]})"
            )
        entity_url = first_url.get(entity, "") or ""
        text = (
            f"{entity} annual financials from web research: "
            + "; ".join(bits)
            + " (snippet-extracted annual figures, labels as reported)."
        )
        source = {
            "title": f"Web research: {entity} annual financials",
            "url": entity_url,
            "provider": "Web research",
        }
        return text, payload_out, source
    except Exception as exc:
        logger.warning("Snippet financial extraction failed for %s: %s", entity, exc)
        return None


def _entities_missing_financials(
    required_entities: list[str], financial_history: list
) -> list[str]:
    """Required entities lacking a usable revenue+net-income annual series."""
    have: set[str] = set()
    for item in financial_history or []:
        if not isinstance(item, dict):
            continue
        revenue = (item.get("revenue", {}) or {}).get("values", [])
        income = (item.get("net_income", {}) or {}).get("values", [])
        if len(revenue or []) >= 2 and len(income or []) >= 2:
            name = str(item.get("entity", "")).strip()
            if name:
                have.add(name.lower())
    return [
        entity for entity in required_entities or []
        if str(entity).strip().lower() not in have
    ]


def _entities_missing_price_history(
    required_entities: list[str], price_history: list
) -> list[str]:
    """Required entities lacking a usable dated price series (generic)."""
    have: set[str] = set()
    for item in price_history or []:
        if not isinstance(item, dict):
            continue
        values = list(item.get("values") or [])
        labels = list(item.get("labels") or [])
        if len(values) >= 2 and len(labels) >= 2:
            name = str(item.get("entity", "")).strip()
            if name:
                have.add(name.lower())
    return [
        entity for entity in required_entities or []
        if str(entity).strip().lower() not in have
    ]


def missing_evidence_requirements(
    entities: list[str],
    metrics: list[str],
    price_history: list,
    financial_history: list,
) -> list[dict]:
    """Generic missing-requirement list (entities x metrics x data type).

    Never includes already-validated cells: only genuinely missing
    requirements are returned for targeted recovery (P0#4).
    """
    try:
        from app.services.data.canonical import derive_evidence_requirements
    except Exception:
        return []
    reqs = derive_evidence_requirements(entities or [], metrics or [], None)
    price_have = {
        str(item.get("entity", "")).strip().lower()
        for item in (price_history or [])
        if isinstance(item, dict) and len(list(item.get("values") or [])) >= 2
    }
    fin_rev_have = set()
    fin_inc_have = set()
    for item in financial_history or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("entity", "")).strip().lower()
        if len(list((item.get("revenue", {}) or {}).get("values", []) or [])) >= 2:
            fin_rev_have.add(name)
        if len(list((item.get("net_income", {}) or {}).get("values", []) or [])) >= 2:
            fin_inc_have.add(name)
    missing: list[dict] = []
    for req in reqs:
        key = req.entity.strip().lower()
        if req.data_type == "market_history":
            if key not in price_have:
                missing.append({"entity": req.entity, "metric": req.metric, "data_type": req.data_type})
        elif req.data_type == "financial_history":
            if req.metric == "revenue_growth" and key not in fin_rev_have:
                missing.append({"entity": req.entity, "metric": req.metric, "data_type": req.data_type})
            elif req.metric == "profitability" and key not in fin_inc_have:
                missing.append({"entity": req.entity, "metric": req.metric, "data_type": req.data_type})
            elif req.metric not in ("revenue_growth", "profitability") and key not in (fin_rev_have & fin_inc_have):
                missing.append({"entity": req.entity, "metric": req.metric, "data_type": req.data_type})
        else:
            # research_figures: snippet recovery handled by the caller via
            # targeted snippet queries (no structured channel to check).
            missing.append({"entity": req.entity, "metric": req.metric, "data_type": req.data_type})
    return missing


async def _research_missing_financials(
    client: httpx.AsyncClient,
    settings,
    missing_entities: list[str],
    history_years: int,
    time_sensitive: bool,
) -> tuple[list[str], list[dict], list[dict]]:
    """Second-pass research for companies the Yahoo pass left uncovered.

    Runs one targeted snippet query per missing company and extracts
    annual revenue / net-income series deterministically. Returns
    (texts, payloads, sources). Bounded, best-effort, never raises.
    """
    texts: list[str] = []
    payloads: list[dict] = []
    sources: list[dict] = []
    for entity in (missing_entities or [])[:MAX_FINANCIAL_RESEARCH_ENTITIES]:
        try:
            query_item = (
                f"{entity} annual revenue net income fiscal year "
                f"last {history_years} years"
            )
            triples = await _snippets_for_query(
                client, query_item, settings, time_sensitive
            )
            extracted = _extract_financials_from_snippets(
                entity, triples, years=history_years
            )
            if extracted is None:
                logger.info(
                    "Second-pass financial research found no usable series for %s.",
                    entity,
                )
                continue
            text, payload, source = extracted
            try:
                from app.services.data.comparison import symbol_for_entity as _sym
                payload["symbol"] = _sym(entity) or payload.get("symbol", "")
            except Exception:
                pass
            texts.append(text)
            payloads.append(payload)
            sources.append(source)
        except Exception as exc:
            logger.warning(
                "Second-pass financial research failed for %s: %s", entity, exc
            )
            continue
    return texts, payloads, sources


async def _snippets_for_query(
    client: httpx.AsyncClient,
    query_item: str,
    settings,
    time_sensitive: bool,
) -> list[tuple]:
    """Run snippet providers concurrently and merge (independent corroboration).

    Tavily (when keyed) + DuckDuckGo always both run; either may fail without
    taking the other down. This is what unlocks provider_count >= 2 downstream.
    """
    tasks = []
    if settings.WEB_SEARCH_API_KEY:
        tasks.append(_tavily_search(client, query_item, settings, time_sensitive))
    tasks.append(_ddg_search(client, query_item, settings))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged: list[tuple] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning(
                "Snippet search failed for %r: %s", query_item, result
            )
            continue
        merged.extend(result or [])
    return merged


async def search_web(
    query: Union[str, list[str]],
    company_name: Optional[str] = None,
    prior_clarification: Optional[str] = None,
    planned_tools: Optional[list[str]] = None,
    *,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    timeframe_label: Optional[str] = None,
    evidence_class: Optional[str] = None,
    provider_version: Optional[str] = None,
) -> WebSearchResult:
    """Return current search snippets for the LLM, never raising to /chat.

    Accepts the raw chat message (reframed internally) or pre-framed queries.
    Repeat queries hit the TTL cache (specs/07 FR5) instead of re-scraping.
    `planned_tools` is the judge's routing verdict (catalog keys): a non-empty
    valid plan dispatches exactly those adapters; None or an empty/invalid
    plan falls back to the deterministic intent predicates - the planner is
    advisory and can never narrow an answer by failing.
    """
    settings = get_settings()
    time_sensitive = False
    if isinstance(query, str):
        framed = await rewrite_search_queries(
            query,
            prior_clarification=prior_clarification,
            company_name=company_name,
        )
        search_queries = framed["queries"][:MAX_SEARCH_QUERIES]
        # Entity-complete routing: the LLM rewriter may drop a company
        # (seen live: "Tesla, BYD, and Toyota" -> only Tesla + BYD
        # researched). Union its entities with the deterministic
        # decomposition so no named company silently disappears from the
        # research stage. Order: LLM first, then deterministic extras.
        entities = _union_entities(
            framed.get("entities", []), query
        )[:MAX_MARKET_ENTITIES]
        time_sensitive = bool(
            framed.get("time_sensitive", False)
            or is_time_sensitive_query(query)
        )
        raw_text = query
    else:
        search_queries = [item for item in query if str(item).strip()][
            :MAX_SEARCH_QUERIES
        ] or [""]
        entities = []
        raw_text = " ".join(search_queries)
        time_sensitive = is_time_sensitive_query(raw_text)

    # Repeat-query cache: same framed evidence reused within TTL for latency,
    # cost, and answer consistency.
    cache_key: Optional[str] = None
    try:
        from app.services.web_search_cache import (
            get_cached_result,
            make_cache_key,
            set_cached_result,
        )

        # Timeframe-aware identity: same entities with a different
        # window (5Y vs 3Y) must never share evidence.
        _tf_label = timeframe_label
        if not _tf_label:
            try:
                from app.services.data.comparison import parse_time_range as _ptr

                _tf_label = (_ptr(raw_text or "").label or "")
            except Exception:
                _tf_label = ""
        cache_key = make_cache_key(
            search_queries, entities, company_name, time_sensitive,
            planned_tools=planned_tools,
            user_id=user_id, thread_id=thread_id,
            timeframe_label=_tf_label,
            evidence_class=evidence_class,
            provider_version=provider_version,
        )
        cached = await get_cached_result(cache_key)
        try:
            from app.services.web_search_cache import (
                is_cached_payload_valid as _valid,
            )

            if cached and not _valid(cached):
                logger.info("Web-search cache entry stale schema; refetching.")
                cached = None
        except Exception:
            pass
        if cached:
            logger.info("Web-search cache hit.")
            return WebSearchResult(
                context=list(cached.get("context", [])),
                sources=list(cached.get("sources", [])),
                market_data=list(cached.get("market_data", [])),
                fundamentals=list(cached.get("fundamentals", [])),
                macro_data=list(cached.get("macro_data", [])),
                price_history=list(cached.get("price_history", [])),
                financial_history=list(cached.get("financial_history", [])),
                research_notes=list(cached.get("research_notes", [])),
            )
    except Exception as exc:
        logger.warning("Web-search cache lookup failed: %s", exc)
        cache_key = None

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            market_texts: list[str] = []
            market_data: list[dict[str, object]] = []
            market_sources: list[dict] = []
            fundamentals: list[dict] = []
            fundamentals_texts: list[str] = []
            fundamentals_sources: list[dict] = []
            macro_texts: list[str] = []
            macro_data: list[dict[str, object]] = []
            macro_sources: list[dict] = []
            price_history: list[dict[str, object]] = []
            price_history_texts: list[str] = []
            price_history_sources: list[dict] = []
            financial_history: list[dict[str, Any]] = []
            financial_history_texts: list[str] = []
            financial_history_sources: list[dict] = []

            query_text = query if isinstance(query, str) else raw_text
            # Tool routing: the canonical ResearchPlan is authoritative.
            # The judge's plan is advisory: resolve_tool_plan() keeps every
            # valid LLM recommendation but unions the deterministic
            # requirements back in, so the LLM may ADD tools but can never
            # silently REMOVE one (e.g. proposing only ["market_history"]
            # for a query that also needs financial_history). Planner
            # abstain/unknown (None) falls back to the deterministic intent
            # predicates below -- a planning failure can never take evidence
            # away.
            planned: Optional[list[str]] = None
            if planned_tools:
                cleaned = [
                    tool for tool in dict.fromkeys(
                        str(item or "").strip().lower() for item in planned_tools
                    )
                    if tool in TOOL_CATALOG
                ]
                try:
                    from app.services.data.comparison import (
                        resolve_tool_plan as _resolve_tool_plan,
                    )
                    resolved = _resolve_tool_plan(cleaned, query_text or "")
                except Exception as exc:
                    logger.warning("Canonical tool-plan resolve failed: %s", exc)
                    resolved = cleaned
                planned = resolved or None
                if planned is not None:
                    if set(planned) != set(cleaned):
                        logger.info(
                            "Canonical plan enforced tools: LLM=%s resolved=%s",
                            cleaned, planned,
                        )
                    else:
                        logger.info("Judge-planned tools: %s", planned)
            history_years = requested_history_years(query_text or "") or 3
            is_historical_request = wants_historical(query_text or "")
            try:
                # Normalized time range (Phase 7): explicit calendar ranges
                # ("2022 to 2025") carry no relative marker, so the regexes
                # above miss them. The structural parse covers both forms
                # and every downstream stage uses it.
                from app.services.data.comparison import (
                    parse_time_range as _parse_time_range,
                )
                _time_range = _parse_time_range(query_text or "")
                if _time_range.years:
                    history_years = max(1, min(int(_time_range.years), 10))
                if _time_range.years and _time_range.years >= 2:
                    is_historical_request = True
            except Exception as exc:
                logger.warning("Time-range normalize failed: %s", exc)
            if planned is not None:
                wants_market = bool(entities and "market" in planned)
                wants_fundamentals = bool(entities and "fundamentals" in planned)
                wants_market_history = bool(entities and "market_history" in planned)
                wants_financial_history = bool(
                    entities and "financial_history" in planned
                )
                wants_wiki = bool(entities and "wikipedia" in planned)
                wants_macro = "macro" in planned
                wants_snippets = "snippets" in planned
                wants_extract = "extract" in planned
                fred_targets = (
                    _detect_fred_series(query_text or "") if wants_macro else []
                )
            else:
                has_market_intent = bool(
                    re.search(MARKET_INTENT_RE, query_text or "", re.IGNORECASE)
                )
                has_fundamentals_intent = bool(
                    FUNDAMENTALS_INTENT_RE.search(query_text or "")
                )
                if not entities and (has_market_intent or has_fundamentals_intent):
                    # Rewrite outage/parse failure must not silently disable
                    # structured evidence: recover candidates deterministically.
                    # Yahoo symbol search validates each downstream.
                    entities = _fallback_entities_from_text(query_text or "")[
                        :MAX_MARKET_ENTITIES
                    ]
                    if entities:
                        logger.info(
                            "Entity fallback recovered candidates: %s", entities
                        )
                # Historical requests need the multi-year adapters INSTEAD of
                # the 1-month snapshot for prices (the snapshot can never
                # satisfy "last N years"). Snapshot fundamentals are still
                # fetched for scale context, but downstream must never treat
                # them as historical growth evidence.
                wants_market_history = bool(
                    entities and is_historical_request and has_market_intent
                )
                wants_financial_history = bool(
                    entities and is_historical_request and has_fundamentals_intent
                )
                wants_market = bool(
                    entities and has_market_intent and not wants_market_history
                )
                wants_fundamentals = bool(entities and has_fundamentals_intent)
                wants_wiki = bool(entities)
                wants_macro = True
                wants_snippets = True
                wants_extract = True
                fred_targets = _detect_fred_series(query_text or "")
                # Deterministic backstop on the fallback path too: the
                # canonical plan's required tools can never be off, even
                # when intent regexes miss (e.g. "profitability" phrasing
                # variants). History always wins over the snapshot for the
                # same evidence kind.
                try:
                    from app.services.data.comparison import (
                        required_tools_for_query as _required_tools,
                    )
                    for _tool in _required_tools(query_text or ""):
                        if _tool == "market_history" and entities:
                            wants_market_history = True
                            wants_market = False
                        elif _tool == "financial_history" and entities:
                            wants_financial_history = True
                        elif _tool == "market" and entities and not wants_market_history:
                            wants_market = True
                        elif _tool == "fundamentals" and entities:
                            wants_fundamentals = True
                        elif _tool == "snippets":
                            wants_snippets = True
                except Exception as exc:
                    logger.warning("Required-tools backstop failed: %s", exc)

            # Entity typing gate (classification before routing): financial
            # entities plus UNKNOWN names (symbol search is their ground-truth
            # typing step; None skips with a reason) enter market adapters.
            # Known non-financial types (concepts, categories, industries,
            # products, geographies, private companies) resolve via
            # snippets/wikipedia/macro only -- never Yahoo.
            try:
                from app.services.data.comparison import (
                    market_candidate_entities as _candidates,
                )

                _market_entities = _candidates(entities or [])
                _skipped = [e for e in (entities or []) if e not in _market_entities]
                if _skipped:
                    logger.info(
                        "Entity typing: non-financial entities skip market "
                        "adapters: %s (financial: %s).",
                        _skipped, _market_entities,
                    )
            except Exception as exc:
                logger.warning("Entity typing failed, using all entities: %s", exc)
                _market_entities = list(entities or [])
            if _market_entities and (
                wants_market
                or wants_fundamentals
                or wants_market_history
                or wants_financial_history
            ):
                for entity in _market_entities:
                    symbol = await _resolve_symbol(client, entity)
                    if not symbol:
                        continue
                    jobs = []
                    if wants_market:
                        jobs.append(_fetch_market_series(client, entity, symbol))
                    if wants_fundamentals:
                        jobs.append(_fetch_fundamentals(client, entity, symbol))
                    if wants_market_history:
                        jobs.append(
                            _fetch_market_history(
                                client, entity, symbol, years=history_years
                            )
                        )
                    if wants_financial_history:
                        jobs.append(
                            _fetch_financial_history(
                                client, entity, symbol, years=history_years
                            )
                        )
                    if not jobs:
                        continue
                    fetched_all = await asyncio.gather(*jobs, return_exceptions=True)
                    for fetched in fetched_all:
                        if isinstance(fetched, Exception) or fetched is None:
                            continue
                        text, payload, source = fetched
                        if not isinstance(payload, dict):
                            continue
                        if "market_cap" in payload:
                            fundamentals_texts.append(text)
                            fundamentals.append(payload)
                            fundamentals_sources.append(source)
                        elif payload.get("is_historical") and (
                            "net_income" in payload or "revenue" in payload
                        ):
                            financial_history_texts.append(text)
                            financial_history.append(payload)
                            financial_history_sources.append(source)
                        elif payload.get("is_historical"):
                            price_history_texts.append(text)
                            price_history.append(payload)
                            price_history_sources.append(source)
                        else:
                            market_texts.append(text)
                            market_data.append(payload)
                            market_sources.append(source)

            if fred_targets:
                fred_results = await asyncio.gather(
                    *(
                        _fetch_fred_series(client, label, series_id, settings)
                        for label, series_id in fred_targets
                    ),
                    return_exceptions=True,
                )
                for fetched in fred_results:
                    if isinstance(fetched, Exception) or fetched is None:
                        continue
                    text, series, source = fetched
                    macro_texts.append(text)
                    macro_data.append(series)
                    macro_sources.append(source)

            snippet_coros = (
                [
                    _snippets_for_query(client, item, settings, time_sensitive)
                    for item in search_queries
                    if item
                ]
                if wants_snippets
                else []
            )
            # Wikipedia grounds every named entity (who/what is X,
            # competitors, industries) from a canonical structured source
            # instead of burning snippet calls on it. Entity-driven, no new
            # intent regex: cheap, free, and concurrent with everything else.
            wiki_coros = (
                [
                    _fetch_wikipedia(client, entity)
                    for entity in entities[:MAX_WIKI_ENTITIES]
                ]
                if wants_wiki
                else []
            )
            # Tavily Extract reads one URL mentioned verbatim in the query
            # in full (follow-ups clearly about a cited article). URL-gated
            # so it never fires - or spends credits - unprompted.
            extract_urls = (
                _find_urls(query_text or "")
                if settings.WEB_SEARCH_API_KEY and wants_extract
                else []
            )
            extract_coros = [
                _tavily_extract(client, url, query_text or "", settings)
                for url in extract_urls
            ]
            fanout = await asyncio.gather(
                *(snippet_coros + wiki_coros + extract_coros),
                return_exceptions=True,
            )
            snippet_lists = fanout[: len(snippet_coros)]
            knowledge_texts: list[str] = []
            knowledge_sources: list[dict] = []
            for fetched in fanout[len(snippet_coros):]:
                if isinstance(fetched, Exception) or fetched is None:
                    continue
                text, source = fetched
                knowledge_texts.append(text)
                knowledge_sources.append(source)
            snippet_triples: list[tuple] = []
            for result in snippet_lists:
                if isinstance(result, Exception):
                    logger.warning("Snippet fan-out failed: %s", result)
                    continue
                snippet_triples.extend(result or [])

            # Merge across queries, deduped by normalized text. Tuples keep
            # context[i] <-> sources[i] aligned 1:1, so citation [n] always
            # resolves to a listed source (URL or provider-only).
            seen_texts: set[str] = set()
            seen_urls: set[str] = set()
            merged_texts: list[str] = []
            merged_sources: list[dict] = []
            for raw_triple in snippet_triples:
                text, url, provider, published_date, score = _coerce_triple(
                    raw_triple
                )
                text_key = " ".join(text.lower().split())
                if not text_key or text_key in seen_texts:
                    continue
                if url and url in seen_urls:
                    continue
                seen_texts.add(text_key)
                if url:
                    seen_urls.add(url)
                merged_texts.append(text)
                source_entry: dict[str, Any] = {
                    "title": text[:80],
                    "url": url,
                    "provider": provider,
                }
                if published_date:
                    source_entry["published_date"] = published_date
                if score is not None:
                    try:
                        source_entry["score"] = float(score)
                    except (TypeError, ValueError):
                        pass
                merged_sources.append(source_entry)
            # Second-pass research: a historical comparison that still
            # lacks annual financials for a required company must keep
            # researching (targeted per-company web queries) -- never stop
            # with 2 of 3 companies and never ask the user to supply
            # public statistics.
            research_notes: list[str] = []
            try:
                from app.services.data.comparison import (
                    detect_metrics as _detect_metrics,
                )
                _metrics = _detect_metrics(query_text or "")
                _needs_revenue = "revenue_growth" in _metrics
                _needs_profit = "profitability" in _metrics
            except Exception:
                _needs_revenue, _needs_profit = False, False
            if (
                is_historical_request
                and entities
                and (_needs_revenue or _needs_profit)
            ):
                missing = _entities_missing_financials(entities, financial_history)
                if missing:
                    research_notes.append(
                        f"Required financial evidence missing for "
                        f"{missing}; running targeted web research."
                    )
                    extra_texts, extra_payloads, extra_sources = (
                        await _research_missing_financials(
                            client, settings, missing,
                            history_years, time_sensitive,
                        )
                    )
                    for text, payload, source in zip(
                        extra_texts, extra_payloads, extra_sources
                    ):
                        financial_history_texts.append(text)
                        financial_history.append(payload)
                        financial_history_sources.append(source)
                    # Generic targeted recovery (P0#4): missing price history
                    # gets one targeted Yahoo retry per missing entity (never
                    # a full refetch of validated series); remaining missing
                    # research-figure requirements get targeted snippet
                    # queries. Validated evidence is never re-retrieved.
                    try:
                        _needs_stock = "stock_performance" in _metrics
                    except Exception:
                        _needs_stock = False
                    if _needs_stock:
                        _missing_price = _entities_missing_price_history(
                            entities, price_history
                        )
                        for _entity in (_missing_price or [])[:4]:
                            try:
                                _symbol = await _resolve_symbol(client, _entity)
                                if not _symbol:
                                    continue
                                _fetched = await _fetch_market_history(
                                    client, _entity, _symbol, years=history_years
                                )
                                if _fetched and isinstance(_fetched[1], dict):
                                    _text, _payload, _source = _fetched
                                    price_history_texts.append(_text)
                                    price_history.append(_payload)
                                    price_history_sources.append(_source)
                            except Exception as _exc:
                                logger.warning(
                                    "Targeted price-history retry failed for %s: %s",
                                    _entity, _exc,
                                )
                                continue
                        if _missing_price:
                            research_notes.append(
                                f"Targeted price-history recovery attempted for "
                                f"{_missing_price}."
                            )
                    still_missing = _entities_missing_financials(
                        entities, financial_history
                    )
                    if still_missing:
                        research_notes.append(
                            f"Annual revenue/net-income history unavailable for "
                            f"{still_missing} from Yahoo Finance and web "
                            f"research; comparison must state this explicitly."
                        )
                    else:
                        research_notes.append(
                            "Second-pass web research completed annual "
                            "financials for all required companies."
                        )
            total_cap = settings.WEB_SEARCH_MAX_RESULTS * max(1, len(search_queries))
            structured_texts = _dedupe_texts(
                market_texts + fundamentals_texts + macro_texts
                + price_history_texts + financial_history_texts
                + knowledge_texts
            )
            context = structured_texts + merged_texts[:total_cap]
            sources = (
                market_sources
                + fundamentals_sources
                + macro_sources
                + price_history_sources
                + financial_history_sources
                + knowledge_sources
                + merged_sources[:total_cap]
            )
            result = WebSearchResult(
                context,
                sources,
                market_data,
                fundamentals,
                macro_data,
                price_history,
                financial_history,
                research_notes,
            )
            if cache_key:
                try:
                    await set_cached_result(
                        cache_key,
                        {
                            "context": result.context,
                            "sources": result.sources,
                            "market_data": result.market_data,
                            "fundamentals": result.fundamentals,
                            "macro_data": result.macro_data,
                            "price_history": result.price_history,
                            "financial_history": result.financial_history,
                            "research_notes": result.research_notes,
                        },
                    )
                except Exception as exc:
                    logger.warning("Web-search cache store failed: %s", exc)
            return result
    except Exception as exc:
        logger.warning("Live web search failed: %s", exc)
        return WebSearchResult([], [], [])

