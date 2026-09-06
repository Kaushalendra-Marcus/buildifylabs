"""Small web-search adapter used by live-web chat requests."""
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from json import JSONDecodeError
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import quote_plus

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

STOCK_SYMBOLS = {
    "Tesla": "TSLA",
    "Reliance": "RELIANCE.NS",
    "Apple": "AAPL",
    "Amazon": "AMZN",
    "Nike": "NKE",
}


@dataclass
class WebSearchResult:
    context: list[str]
    sources: list[dict[str, str]]
    market_data: list[dict[str, object]]


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[str] = []
        self.sources: list[str] = []
        self._capture = False
        self._buffer: list[str] = []
        self._kind = ""
        self._capture_tag = ""
        self._source_url = ""

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

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag == self._capture_tag:
            text = " ".join("".join(self._buffer).split())
            if text:
                self.results.append(html.unescape(text))
                if self._source_url:
                    self.sources.append(self._source_url)
            self._capture = False
            self._kind = ""
            self._capture_tag = ""
            self._source_url = ""


async def search_web(query: str, company_name: Optional[str] = None) -> WebSearchResult:
    """Return current search snippets for the LLM, never raising to /chat."""
    settings = get_settings()
    original_query = f"{company_name} {query}" if company_name else query
    search_query = original_query
    known_entities: list[str] = []
    query_lower = original_query.lower()
    if any(term in query_lower for term in ("sales", "revenue", "income", "profit")):
        search_query = f"{search_query} annual report financial results revenue"

    search_queries = [search_query]
    comparison = re.search(
        r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:\s+shoes?)?\??$",
        original_query,
        re.IGNORECASE,
    )
    if comparison and any(term in query_lower for term in ("sales", "revenue")):
        search_queries = [
            f"{comparison.group(1).strip()} revenue annual report financial results",
            f"{comparison.group(2).strip()} revenue annual report financial results",
        ]
    elif any(term in query_lower for term in ("stock", "stocks", "share", "rate")):
        known_entities = [
            entity
            for entity in STOCK_SYMBOLS
            if re.search(rf"\b{entity}\b", original_query, re.IGNORECASE)
        ]
        if known_entities:
            search_queries = [
                f"{entity} stock price performance last one month historical price"
                for entity in known_entities
            ]

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            market_results = []
            market_data: list[dict[str, object]] = []
            sources: list[dict[str, str]] = []
            for entity in known_entities:
                try:
                    symbol = STOCK_SYMBOLS[entity]
                    response = await client.get(
                        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d",
                        headers={"User-Agent": "BuildifyLabs/1.0", "Accept": "application/json"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    chart = payload["chart"]["result"][0]
                    closes = [
                        value
                        for value in chart["indicators"]["quote"][0]["close"]
                        if value is not None
                    ]
                    if len(closes) >= 2 and closes[0] != 0:
                        change = (closes[-1] - closes[0]) / closes[0] * 100
                        currency = chart["meta"].get("currency", "")
                        market_results.append(
                            f"{entity} ({symbol}) one-month market data: "
                            f"first close {closes[0]:.2f} {currency}, "
                            f"latest close {closes[-1]:.2f} {currency}, "
                            f"change {change:+.2f}% (computed from Yahoo Finance daily closes)."
                        )
                        timestamps = chart.get("timestamp", [])
                        labels = [
                            datetime.fromtimestamp(timestamp, timezone.utc).strftime("%b %d")
                            for timestamp in timestamps[-len(closes):]
                        ]
                        market_data.append(
                            {
                                "entity": entity,
                                "symbol": symbol,
                                "currency": currency,
                                "labels": labels,
                                "values": closes,
                            }
                        )
                        sources.append(
                            {
                                "title": f"Yahoo Finance: {entity} ({symbol}) historical data",
                                "url": f"https://finance.yahoo.com/quote/{symbol}/history/",
                                "provider": "Yahoo Finance",
                            }
                        )
                except (KeyError, IndexError, JSONDecodeError, ValueError, httpx.HTTPError) as exc:
                    logger.warning("Market data lookup failed for %s: %s", entity, exc)

            if settings.WEB_SEARCH_API_KEY:
                results = []
                for query_item in search_queries:
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
                    if payload.get("answer"):
                        results.append(payload["answer"])
                    results.extend(
                        f"{item.get('title', '')}: {item.get('content', '')}"
                        for item in payload.get("results", [])
                        if item.get("content")
                    )
                    sources.extend(
                        {
                            "title": item.get("title", "Web result"),
                            "url": item.get("url", "https://tavily.com/"),
                            "provider": "Tavily",
                        }
                        for item in payload.get("results", [])
                        if item.get("url")
                    )
                return WebSearchResult(
                    market_results + results[: settings.WEB_SEARCH_MAX_RESULTS * len(search_queries)],
                    sources,
                    market_data,
                )

            results = []
            for query_item in search_queries:
                response = await client.get(
                    f"https://html.duckduckgo.com/html/?q={quote_plus(query_item)}",
                    headers={"User-Agent": "BuildifyLabs/1.0"},
                )
                response.raise_for_status()
                parser = _DuckDuckGoParser()
                parser.feed(response.text)
                results.extend(parser.results[: settings.WEB_SEARCH_MAX_RESULTS])
                sources.extend(
                    {
                        "title": result,
                        "url": url,
                        "provider": "DuckDuckGo",
                    }
                    for result, url in zip(parser.results, parser.sources)
                )
            if not results and search_query != original_query:
                response = await client.get(
                    f"https://html.duckduckgo.com/html/?q={quote_plus(original_query)}",
                    headers={"User-Agent": "BuildifyLabs/1.0"},
                )
                response.raise_for_status()
                parser = _DuckDuckGoParser()
                parser.feed(response.text)
                results = parser.results[: settings.WEB_SEARCH_MAX_RESULTS]
                sources.extend(
                    {
                        "title": result,
                        "url": url,
                        "provider": "DuckDuckGo",
                    }
                    for result, url in zip(parser.results, parser.sources)
                )
            return WebSearchResult(market_results + results, sources, market_data)
    except Exception as exc:
        logger.warning("Live web search failed: %s", exc)
        return WebSearchResult([], [], [])