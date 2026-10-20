"""Firecrawl is the managed snippet provider that replaced DuckDuckGo's HTML
scrape. These tests pin the mapping into the pipeline's 5-tuple contract and
the fail-open merge behaviour (one provider down must not kill the other)."""
import asyncio
from types import SimpleNamespace

import app.services.web_search as web_search_mod


def _settings(**overrides):
    values = {
        "FIRECRAWL_API_KEY": "test-key",
        "FIRECRAWL_MAX_RESULTS": 5,
        "WEB_SEARCH_MAX_RESULTS": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeFirecrawl:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.data


def test_maps_markdown_document_to_provider_tuple(monkeypatch):
    doc = SimpleNamespace(
        markdown="# AI market\n\nThe market was worth $375.93 billion in 2026.",
        metadata=SimpleNamespace(
            title="Artificial Intelligence Market",
            source_url="https://example.com/ai-market",
            description="Market size and forecast.",
            published_time="2026-09-01T10:30:00Z",
        ),
    )
    fake = _FakeFirecrawl(
        SimpleNamespace(web=[doc], news=[], warning=None)
    )
    monkeypatch.setattr(
        web_search_mod, "_get_firecrawl_client", lambda settings: fake
    )

    triples = asyncio.run(
        web_search_mod._firecrawl_search(None, "AI market size", _settings())
    )

    assert len(triples) == 1
    text, url, provider, published, score = triples[0]
    assert provider == "Firecrawl"
    assert url == "https://example.com/ai-market"
    assert text.startswith("Artificial Intelligence Market:")
    assert "$375.93 billion" in text
    assert published == "2026-09-01"
    # Scraped Documents have no position; the adapter falls back to search
    # order (first result -> 1.0) so context_budget can rank it.
    assert score == 1.0
    assert fake.calls[0][1]["sources"] == ["web"]
    assert fake.calls[0][1]["limit"] == 5


def test_time_sensitive_uses_news_and_keeps_date(monkeypatch):
    news = SimpleNamespace(
        title="AI investment jumps",
        url="https://example.com/news",
        snippet="Global AI investment rose in 2026.",
        date="2026-10-01",
    )
    fake = _FakeFirecrawl(
        SimpleNamespace(news=[news], web=[], warning=None)
    )
    monkeypatch.setattr(
        web_search_mod, "_get_firecrawl_client", lambda settings: fake
    )

    triples = asyncio.run(
        web_search_mod._firecrawl_search(
            None, "latest AI investment", _settings(), time_sensitive=True
        )
    )

    assert triples[0][2] == "Firecrawl"
    assert triples[0][3] == "2026-10-01"
    assert fake.calls[0][1]["sources"] == ["news"]


def test_missing_key_disables_provider_without_calling_sdk(monkeypatch):
    def _must_not_run(settings):  # pragma: no cover
        raise AssertionError("Firecrawl client must not be created without a key")

    monkeypatch.setattr(
        web_search_mod, "_get_firecrawl_client", _must_not_run
    )

    assert asyncio.run(
        web_search_mod._firecrawl_search(
            None, "query", _settings(FIRECRAWL_API_KEY=None)
        )
    ) == []


def test_firecrawl_failure_does_not_kill_tavily(monkeypatch):
    async def fake_tavily(client, query_item, settings, time_sensitive=False):
        return [("tavily result", "https://t.example", "Tavily", None, 0.9)]

    async def boom_firecrawl(client, query_item, settings, time_sensitive=False):
        raise RuntimeError("firecrawl down")

    monkeypatch.setattr(web_search_mod, "_tavily_search", fake_tavily)
    monkeypatch.setattr(web_search_mod, "_firecrawl_search", boom_firecrawl)

    result = asyncio.run(
        web_search_mod._snippets_for_query(
            None,
            "query",
            _settings(WEB_SEARCH_API_KEY="tavily-key"),
            time_sensitive=False,
        )
    )

    assert result == [("tavily result", "https://t.example", "Tavily", None, 0.9)]
