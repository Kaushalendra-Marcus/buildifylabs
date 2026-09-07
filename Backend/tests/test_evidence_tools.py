"""Evidence-tool adapters: Wikipedia grounding, Yahoo fundamentals (crumb),
FRED macro series, Tavily Extract, and the deterministic what-if calculator.

HTTP is faked at the client seam (duck-typed stand-ins for httpx.AsyncClient);
the LLM rewriter is bypassed for search_web-level merge tests. Nothing here
touches the network - every adapter must degrade to None, never raise.
"""
import asyncio

import httpx
import pytest

import app.services.web_search as web_search_mod
from app.services import web_search_cache
from app.services.data.stats import apply_what_if, parse_what_if
from app.services.web_search import (
    _fetch_fundamentals,
    _fetch_fred_series,
    _fetch_wikipedia,
    _find_urls,
    _tavily_extract,
    search_web,
)


class FakeResponse:
    def __init__(self, *, json_data=None, text_data="", status_code=200):
        self._json_data = json_data if json_data is not None else {}
        self.text = text_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"fake status {self.status_code}")

    def json(self):
        return self._json_data


class FakeClient:
    """Routes (method, url-substring) -> handler(url, kwargs) -> FakeResponse."""

    def __init__(self, routes):
        self._routes = list(routes)
        self.calls: list[tuple[str, str, dict]] = []
        self.cookies: dict = {}

    async def _dispatch(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for route_method, needle, handler in self._routes:
            if route_method == method and needle in url:
                return await handler(url, kwargs)
        raise httpx.HTTPError(f"no fake route for {method} {url}")

    async def get(self, url, **kwargs):
        return await self._dispatch("GET", url, kwargs)

    async def post(self, url, **kwargs):
        return await self._dispatch("POST", url, kwargs)


@pytest.fixture(autouse=True)
def _reset_shared_state():
    web_search_mod._yahoo_crumb.update({"crumb": None, "cookie": "", "at": 0.0})
    web_search_cache._reset_cache_state()
    yield
    web_search_mod._yahoo_crumb.update({"crumb": None, "cookie": "", "at": 0.0})
    web_search_cache._reset_cache_state()


def _wiki_summary(title="Acme Corp", extract="Acme Corp is a fictional company."):
    return {
        "type": "standard",
        "title": title,
        "extract": extract,
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Acme_Corp"}},
    }


def _quote_summary(market_cap=1_000_000, pe=25.5, revenue=500_000):
    return {
        "quoteSummary": {
            "result": [
                {
                    "price": {
                        "currency": {"raw": "USD"},
                        "marketCap": {"raw": market_cap},
                    },
                    "defaultKeyStatistics": {"trailingPE": {"raw": pe}},
                    "summaryDetail": {},
                    "financialData": {"totalRevenue": {"raw": revenue}},
                }
            ]
        }
    }


class TestWikipedia:
    def test_summary_returns_text_and_source(self):
        async def wiki(url, kwargs):
            return FakeResponse(json_data=_wiki_summary())

        client = FakeClient([("GET", "wikipedia.org", wiki)])
        text, source = asyncio.run(_fetch_wikipedia(client, "Acme Corp"))
        assert "Acme Corp" in text
        assert "fictional company" in text
        assert "(Wikipedia summary.)" in text
        assert source["provider"] == "Wikipedia"
        assert source["url"] == "https://en.wikipedia.org/wiki/Acme_Corp"

    def test_disambiguation_is_skipped(self):
        async def wiki(url, kwargs):
            payload = _wiki_summary()
            payload["type"] = "disambiguation"
            return FakeResponse(json_data=payload)

        client = FakeClient([("GET", "wikipedia.org", wiki)])
        assert asyncio.run(_fetch_wikipedia(client, "Acme")) is None

    def test_missing_page_returns_none(self):
        async def wiki(url, kwargs):
            return FakeResponse(status_code=404)

        client = FakeClient([("GET", "wikipedia.org", wiki)])
        assert asyncio.run(_fetch_wikipedia(client, "No Such Page Xyz")) is None

    def test_transport_failure_never_raises(self):
        async def wiki(url, kwargs):
            raise httpx.HTTPError("down")

        client = FakeClient([("GET", "wikipedia.org", wiki)])
        assert asyncio.run(_fetch_wikipedia(client, "Acme")) is None


class TestFundamentalsCrumb:
    def _handshake_routes(self, crumb="crumb123", summary=None):
        async def fc(url, kwargs):
            return FakeResponse(status_code=200)

        async def getcrumb(url, kwargs):
            return FakeResponse(text_data=crumb)

        async def quote(url, kwargs):
            return FakeResponse(json_data=summary or _quote_summary())

        return [
            ("GET", "fc.yahoo.com", fc),
            ("GET", "getcrumb", getcrumb),
            ("GET", "quoteSummary", quote),
        ]

    def test_crumb_sent_and_numbers_parsed(self):
        client = FakeClient(self._handshake_routes())
        fetched = asyncio.run(_fetch_fundamentals(client, "Acme", "ACME"))
        assert fetched is not None
        text, fundamentals, source = fetched
        assert fundamentals["market_cap"] == 1_000_000
        assert fundamentals["pe_ratio"] == 25.5
        assert fundamentals["revenue"] == 500_000
        assert "Acme (ACME)" in text
        assert source["provider"] == "Yahoo Finance"
        quote_calls = [c for c in client.calls if "quoteSummary" in c[1]]
        assert len(quote_calls) == 1
        params = quote_calls[0][2].get("params", {})
        assert params.get("crumb") == "crumb123"
        assert "defaultKeyStatistics" in params.get("modules", "")

    def test_401_retries_once_with_fresh_crumb(self):
        state = {"quotes": 0}

        async def fc(url, kwargs):
            return FakeResponse(status_code=200)

        async def getcrumb(url, kwargs):
            return FakeResponse(text_data="fresh-crumb")

        async def quote(url, kwargs):
            state["quotes"] += 1
            if state["quotes"] == 1:
                return FakeResponse(status_code=401)
            return FakeResponse(json_data=_quote_summary())

        client = FakeClient(
            [
                ("GET", "fc.yahoo.com", fc),
                ("GET", "getcrumb", getcrumb),
                ("GET", "quoteSummary", quote),
            ]
        )
        fetched = asyncio.run(_fetch_fundamentals(client, "Acme", "ACME"))
        assert fetched is not None
        assert fetched[1]["market_cap"] == 1_000_000
        assert state["quotes"] == 2

    def test_handshake_failure_skips_without_quote_call(self):
        async def getcrumb(url, kwargs):
            raise httpx.HTTPError("down")

        async def quote(url, kwargs):  # pragma: no cover - must not run
            raise AssertionError("quoteSummary must not be called without a crumb")

        client = FakeClient(
            [
                ("GET", "getcrumb", getcrumb),
                ("GET", "quoteSummary", quote),
            ]
        )
        assert asyncio.run(_fetch_fundamentals(client, "Acme", "ACME")) is None

    def test_empty_numbers_return_none(self):
        routes = self._handshake_routes(
            summary={"quoteSummary": {"result": [{}]}}
        )
        client = FakeClient(routes)
        assert asyncio.run(_fetch_fundamentals(client, "Acme", "ACME")) is None


class TestFred:
    def test_skips_without_api_key(self):
        from types import SimpleNamespace

        async def obs(url, kwargs):  # pragma: no cover - must not run
            raise AssertionError("FRED must not be called without a key")

        client = FakeClient([("GET", "stlouisfed", obs)])
        settings = SimpleNamespace(FRED_API_KEY=None)
        assert (
            asyncio.run(_fetch_fred_series(client, "L", "CPIAUCSL", settings))
            is None
        )

    def test_observations_parse_latest_and_prior(self):
        from types import SimpleNamespace

        async def obs(url, kwargs):
            # API returns sort_order=desc; adapter restores chronology.
            return FakeResponse(
                json_data={
                    "observations": [
                        {"date": "2024-02-01", "value": "301.5"},
                        {"date": "2024-01-01", "value": "300.0"},
                    ]
                }
            )

        client = FakeClient([("GET", "stlouisfed", obs)])
        settings = SimpleNamespace(FRED_API_KEY="k")
        fetched = asyncio.run(
            _fetch_fred_series(client, "US Consumer Price Index", "CPIAUCSL", settings)
        )
        assert fetched is not None
        text, series, source = fetched
        assert series["labels"] == ["2024-01-01", "2024-02-01"]
        assert series["values"] == [300.0, 301.5]
        assert "301.5" in text
        assert source["provider"] == "FRED"


class TestExtract:
    def test_find_urls_single_and_strips_punctuation(self):
        assert _find_urls("read https://a.example/x, please.") == [
            "https://a.example/x"
        ]
        assert _find_urls("no links here") == []

    def test_extract_returns_truncated_content(self):
        from types import SimpleNamespace

        async def extract(url, kwargs):
            body = kwargs.get("json", {})
            assert body.get("extract_depth") == "basic"
            assert body.get("urls") == "https://a.example/x"
            return FakeResponse(
                json_data={"results": [{"raw_content": "z" * 5000}]}
            )

        client = FakeClient([("POST", "/extract", extract)])
        settings = SimpleNamespace(WEB_SEARCH_API_KEY="k")
        fetched = asyncio.run(
            _tavily_extract(client, "https://a.example/x", "what does it say?", settings)
        )
        assert fetched is not None
        text, source = fetched
        assert len(text) <= 4600
        assert source["provider"] == "Tavily Extract"
        assert source["url"] == "https://a.example/x"

    def test_extract_skips_without_key(self):
        from types import SimpleNamespace

        async def extract(url, kwargs):  # pragma: no cover - must not run
            raise AssertionError("extract must not be called without a key")

        client = FakeClient([("POST", "/extract", extract)])
        settings = SimpleNamespace(WEB_SEARCH_API_KEY=None)
        assert (
            asyncio.run(_tavily_extract(client, "https://a.example/x", "q", settings))
            is None
        )


class TestSearchWebMerge:
    def _rewrite_fake(self, queries, entities):
        async def fake(query, prior_clarification=None, company_name=None):
            return {
                "queries": queries,
                "entities": entities,
                "time_sensitive": False,
            }

        return fake

    async def _no_snippets(self, client, query_item, settings):
        return []

    def test_wiki_text_lands_in_context_and_sources(self, monkeypatch):
        monkeypatch.setattr(
            web_search_mod, "rewrite_search_queries", self._rewrite_fake(["q1"], ["Acme"])
        )
        monkeypatch.setattr(web_search_mod, "_ddg_search", self._no_snippets)
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None
        )
        monkeypatch.setattr(
            web_search_mod.get_settings(), "FRED_API_KEY", None
        )

        async def fake_wiki(client, entity):
            return (
                "Acme Corp: a fictional company (Wikipedia summary.)",
                {
                    "title": "Wikipedia: Acme Corp",
                    "url": "https://en.wikipedia.org/wiki/Acme_Corp",
                    "provider": "Wikipedia",
                },
            )

        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", fake_wiki)
        result = asyncio.run(search_web("Who is Acme?"))
        assert result.context[0].startswith("Acme Corp:")
        assert result.sources[0]["provider"] == "Wikipedia"

    def test_url_in_query_triggers_extract(self, monkeypatch):
        monkeypatch.setattr(
            web_search_mod,
            "rewrite_search_queries",
            self._rewrite_fake(["read the article"], []),
        )
        monkeypatch.setattr(web_search_mod, "_ddg_search", self._no_snippets)

        async def fake_tavily(client, query_item, settings, time_sensitive=False):
            return []

        monkeypatch.setattr(web_search_mod, "_tavily_search", fake_tavily)
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", "k"
        )

        async def fake_extract(client, url, query, settings):
            return (
                f"Full content from {url}: hello (Tavily Extract.)",
                {
                    "title": f"Extracted: {url[:80]}",
                    "url": url,
                    "provider": "Tavily Extract",
                },
            )

        monkeypatch.setattr(web_search_mod, "_tavily_extract", fake_extract)
        result = asyncio.run(search_web("summarize https://a.example/x please"))
        assert "Full content from https://a.example/x" in result.context[0]
        assert result.sources[0]["provider"] == "Tavily Extract"


class TestWhatIf:
    def test_parse_raise(self):
        assert parse_what_if("What if I raise price 10%?") == ("price", 10.0)

    def test_parse_drop(self):
        assert parse_what_if("what happens if price drops 5%?") == ("price", -5.0)

    def test_parse_discount_inverts(self):
        assert parse_what_if("What if I increase discount by 20%?") == (
            "price",
            -20.0,
        )

    def test_parse_no_intent(self):
        assert parse_what_if("how is revenue trending?") is None

    def test_apply_price_times_quantity(self):
        rows = [
            {"unit_price": 10.0, "qty": 5},
            {"unit_price": 20.0, "qty": 2},
        ]
        scenario = apply_what_if(rows, "price", 10.0)
        assert scenario is not None
        assert scenario["baseline_total"] == 90.0
        assert scenario["scenario_total"] == 99.0
        assert scenario["delta"] == 9.0
        assert "quantity is unaffected" in scenario["assumption"]

    def test_apply_without_price_column_returns_none(self):
        rows = [{"region": "east", "revenue": 100}]
        assert apply_what_if(rows, "price", 10.0) is None

    def test_apply_negative_change(self):
        rows = [{"price": 100.0, "quantity": 1}]
        scenario = apply_what_if(rows, "price", -10.0)
        assert scenario is not None
        assert scenario["scenario_total"] == 90.0
        assert scenario["delta"] == -10.0
