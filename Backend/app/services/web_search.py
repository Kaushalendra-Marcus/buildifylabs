"""Web-search adapter used by live-web chat requests.

The chat message is first reframed into clean search queries (LLM, generic -
see `app.services.llm.query_rewriter`), then fanned out: each query runs
against the snippet provider, community discussion is covered by a
site-restricted variant when the rewriter emits one, and market series resolve
for any named entity via Yahoo's symbol search (no hardcoded company list).
Results merge with per-URL/per-text dedupe. Never raises to /chat.
"""
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from json import JSONDecodeError
from html.parser import HTMLParser
from typing import Optional, Union
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.services.llm.query_rewriter import rewrite_search_queries

logger = logging.getLogger(__name__)

# Fast-path aliases so common names skip the symbol-search round trip.
STOCK_ALIASES = {
    "Tesla": "TSLA",
    "Reliance": "RELIANCE.NS",
    "Apple": "AAPL",
    "Amazon": "AMZN",
    "Nike": "NKE",
}

MAX_SEARCH_QUERIES = 3
MAX_MARKET_ENTITIES = 2

# Generic financial-intent gate for market-series lookup (intents, not names).
MARKET_INTENT_RE = (
    r"\b(stock|stocks|share|shares|price|prices|market cap|marketcap|"
    r"ticker|ipo|valuation|nasdaq|nyse|nse|bse)\b"
)

_HEADERS = {"User-Agent": "BuildifyLabs/1.0"}


@dataclass
class WebSearchResult:
    context: list[str]
    sources: list[dict[str, str]]
    market_data: list[dict[str, object]]


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


async def _tavily_search(
    client: httpx.AsyncClient, query_item: str, settings
) -> list[tuple[str, str, str]]:
    """Returns (text, url-or-"", provider) triples, answer first."""
    response = await client.post(
        "https://api.tavily.com/search",
        json={
            "api_key": settings.WEB_SEARCH_API_KEY,
            "query": query_item,
            "search_depth": "basic",
            "max_results": settings.WEB_SEARCH_MAX_RESULTS,
            "include_answer": True,
        },
    )
    response.raise_for_status()
    payload = response.json()
    triples: list[tuple[str, str, str]] = []
    if payload.get("answer"):
        triples.append((str(payload["answer"]), "", "Tavily"))
    for item in payload.get("results", []):
        if not item.get("content"):
            continue
        triples.append(
            (
                f"{item.get('title', '')}: {item.get('content', '')}",
                str(item.get("url", "")),
                "Tavily",
            )
        )
    return triples


async def _ddg_search(
    client: httpx.AsyncClient, query_item: str, settings
) -> list[tuple[str, str, str]]:
    """Returns (text, url-or-"", provider) triples from merged title/snippet pairs."""
    response = await client.get(
        f"https://html.duckduckgo.com/html/?q={quote_plus(query_item)}",
        headers=_HEADERS,
    )
    response.raise_for_status()
    parser = _DuckDuckGoParser()
    parser.feed(response.text)
    parser.close()
    return [
        (text, url, "DuckDuckGo")
        for text, url in parser.pairs[: settings.WEB_SEARCH_MAX_RESULTS]
    ]


async def search_web(
    query: Union[str, list[str]],
    company_name: Optional[str] = None,
    prior_clarification: Optional[str] = None,
) -> WebSearchResult:
    """Return current search snippets for the LLM, never raising to /chat.

    Accepts the raw chat message (reframed internally) or pre-framed queries.
    """
    settings = get_settings()
    if isinstance(query, str):
        framed = await rewrite_search_queries(
            query,
            prior_clarification=prior_clarification,
            company_name=company_name,
        )
        search_queries = framed["queries"][:MAX_SEARCH_QUERIES]
        entities = framed["entities"][:MAX_MARKET_ENTITIES]
    else:
        search_queries = [item for item in query if str(item).strip()][
            :MAX_SEARCH_QUERIES
        ] or [""]
        entities = []

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            market_texts: list[str] = []
            market_data: list[dict[str, object]] = []
            market_sources: list[dict] = []
            if entities and re.search(MARKET_INTENT_RE, query if isinstance(query, str) else "", re.IGNORECASE):
                for entity in entities:
                    symbol = await _resolve_symbol(client, entity)
                    if not symbol:
                        continue
                    fetched = await _fetch_market_series(client, entity, symbol)
                    if fetched is None:
                        continue
                    text, series, source = fetched
                    market_texts.append(text)
                    market_data.append(series)
                    market_sources.append(source)

            snippet_triples: list[tuple[str, str, str]] = []
            for query_item in search_queries:
                if not query_item:
                    continue
                try:
                    if settings.WEB_SEARCH_API_KEY:
                        triples = await _tavily_search(
                            client, query_item, settings
                        )
                    else:
                        triples = await _ddg_search(
                            client, query_item, settings
                        )
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    logger.warning("Snippet search failed for %r: %s", query_item, exc)
                    continue
                snippet_triples.extend(triples)

            # Merge across queries, deduped by normalized text. Triples keep
            # context[i] <-> sources[i] aligned 1:1, so citation [n] always
            # resolves to a listed source (URL or provider-only).
            seen_texts: set[str] = set()
            seen_urls: set[str] = set()
            merged_texts: list[str] = []
            merged_sources: list[dict] = []
            for text, url, provider in snippet_triples:
                text_key = " ".join(text.lower().split())
                if not text_key or text_key in seen_texts:
                    continue
                if url and url in seen_urls:
                    continue
                seen_texts.add(text_key)
                if url:
                    seen_urls.add(url)
                merged_texts.append(text)
                merged_sources.append(
                    {
                        "title": text[:80],
                        "url": url,
                        "provider": provider,
                    }
                )
            total_cap = settings.WEB_SEARCH_MAX_RESULTS * max(1, len(search_queries))
            context = _dedupe_texts(market_texts) + merged_texts[:total_cap]
            sources = market_sources + merged_sources[:total_cap]
            return WebSearchResult(context, sources, market_data)
    except Exception as exc:
        logger.warning("Live web search failed: %s", exc)
        return WebSearchResult([], [], [])
