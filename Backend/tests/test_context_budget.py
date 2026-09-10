"""Evidence ranking + budget (specs/07 hardening, Phase 3).

Covers context_budget helpers (estimate/rank/fit) and the search_web
integration (over-budget snippet pools are ranked, trimmed, and disclosed).
"""
import asyncio
from datetime import datetime, timezone

import app.services.web_search as web_search_mod
from app.services.llm.context_budget import (
    estimate_tokens,
    fit_pairs_to_budget,
    rank_snippet_pairs,
)


class TestEstimateTokens:
    def test_empty_string_is_zero(self):
        assert estimate_tokens("") == 0

    def test_400_chars_is_100_tokens(self):
        assert estimate_tokens("a" * 400) == 100


class TestRankSnippetPairs:
    def test_low_relevance_sorts_last_and_pairs_stay_aligned(self):
        today = datetime.now(timezone.utc).date().isoformat()
        texts = [
            "unrelated filler about cooking recipes pasta",
            "fuel prices market update today",
            "market fuel prices surge with details",
        ]
        sources = [
            {"title": "low", "url": "https://low.example", "provider": "X"},
            {
                "title": "recent",
                "url": "https://recent.example",
                "provider": "X",
                "published_date": today,
            },
            {
                "title": "high",
                "url": "https://high.example",
                "provider": "X",
                "score": 0.95,
                "published_date": today,
            },
        ]
        ranked_texts, ranked_sources = rank_snippet_pairs(
            texts, sources, "fuel prices market"
        )
        # Low-relevance (no score, no date, no term overlap) sorts last.
        assert ranked_texts[-1] == "unrelated filler about cooking recipes pasta"
        assert ranked_sources[-1]["url"] == "https://low.example"
        # Pairing preserved: each text stays next to its own source.
        for text, source in zip(ranked_texts, ranked_sources):
            if "fuel prices market update" in text:
                assert source["url"] == "https://recent.example"
            if "market fuel prices surge" in text:
                assert source["url"] == "https://high.example"

    def test_length_mismatch_returns_unchanged(self):
        texts = ["a", "b"]
        sources = [{"title": "only"}]
        out_texts, out_sources = rank_snippet_pairs(texts, sources, "q")
        assert out_texts == texts
        assert out_sources == sources


class TestFitPairsToBudget:
    def test_all_kept_when_well_under_budget(self):
        texts = ["a", "bb", "ccc", "dddd", "eeeee"]
        sources = [{"title": str(i)} for i in range(5)]
        kept_texts, kept_sources, dropped = fit_pairs_to_budget(
            texts, sources, 100000
        )
        assert kept_texts == texts
        assert kept_sources == sources
        assert dropped == 0

    def test_stops_where_item_pushes_over_budget(self):
        texts = ["a" * 10, "b" * 10, "c" * 10, "d" * 100, "e" * 10]
        sources = [{"title": str(i)} for i in range(5)]
        # 10+20=30 each for first three (used=90); fourth costs 120 -> over 100.
        kept_texts, _kept_sources, dropped = fit_pairs_to_budget(
            texts, sources, 100
        )
        assert kept_texts == texts[:3]
        assert dropped == 2

    def test_zero_budget_drops_everything_without_error(self):
        texts = ["a", "b"]
        sources = [{"title": "a"}, {"title": "b"}]
        kept_texts, _kept_sources, dropped = fit_pairs_to_budget(
            texts, sources, 0
        )
        assert kept_texts == []
        assert dropped == len(texts)


class TestSearchWebBudgetIntegration:
    def test_over_budget_pool_is_trimmed_and_disclosed(self, monkeypatch):
        import app.services.llm.query_rewriter as rewriter_mod

        async def fake_rewrite(user_query, prior_clarification=None, company_name=None):
            return {
                "queries": ["fuel prices market"],
                "entities": [],
                "time_sensitive": False,
            }

        monkeypatch.setattr(
            rewriter_mod, "generate_response", lambda *a, **k: None
        )
        monkeypatch.setattr(
            web_search_mod, "rewrite_search_queries", fake_rewrite
        )

        async def fake_snippets(client, query_item, settings, time_sensitive):
            triples = []
            for i in range(30):
                triples.append(
                    (
                        f"fuel prices market result {i} " + ("x" * 2000),
                        f"https://example.com/{i}",
                        "DuckDuckGo",
                        None,
                        None,
                    )
                )
            return triples

        monkeypatch.setattr(web_search_mod, "_snippets_for_query", fake_snippets)
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None
        )
        monkeypatch.setattr(
            web_search_mod.get_settings(), "MAX_EVIDENCE_CONTEXT_CHARS", 12000
        )

        result = asyncio.run(web_search_mod.search_web("fuel prices market news"))
        total_chars = sum(len(t or "") for t in result.context)
        assert total_chars <= 12000
        assert any("Trimmed" in note for note in result.research_notes)
        assert len(result.context) == len(result.sources)

    def test_structured_evidence_survives_whole_pool_budget(self, monkeypatch):
        async def fake_rewrite(user_query, prior_clarification=None, company_name=None):
            return {
                "queries": ["Acme stock price"],
                "entities": ["Acme"],
                "time_sensitive": False,
            }

        monkeypatch.setattr(
            web_search_mod, "rewrite_search_queries", fake_rewrite
        )

        async def fake_resolve(client, entity):
            return "ACME"

        async def fake_market(client, entity, symbol):
            text = "Acme (ACME) one-month market data summary. " + ("m" * 9000)
            series = {
                "entity": entity,
                "symbol": symbol,
                "currency": "USD",
                "labels": ["Sep 01", "Sep 02"],
                "values": [10.0, 11.0],
            }
            source = {
                "title": "Yahoo Finance: Acme (ACME) historical data",
                "url": "https://finance.yahoo.com/quote/ACME/history/",
                "provider": "Yahoo Finance",
            }
            return text, series, source

        async def fake_wiki(client, entity):
            return None

        async def fake_snippets(client, query_item, settings, time_sensitive):
            return [
                (
                    f"Acme stock price snippet {i} " + ("s" * 2000),
                    f"https://example.com/acme/{i}",
                    "DuckDuckGo",
                    None,
                    None,
                )
                for i in range(20)
            ]

        monkeypatch.setattr(web_search_mod, "_resolve_symbol", fake_resolve)
        monkeypatch.setattr(web_search_mod, "_fetch_market_series", fake_market)
        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", fake_wiki)
        monkeypatch.setattr(web_search_mod, "_snippets_for_query", fake_snippets)
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None
        )
        monkeypatch.setattr(
            web_search_mod.get_settings(), "MAX_EVIDENCE_CONTEXT_CHARS", 12000
        )

        result = asyncio.run(web_search_mod.search_web("Acme stock price budget check"))
        total_chars = sum(len(t or "") for t in result.context)
        assert total_chars <= 12000
        # Structured (market) evidence is first and never trimmed away.
        assert result.context
        assert result.context[0].startswith("Acme (ACME) one-month market data")
        assert result.sources[0]["provider"] == "Yahoo Finance"
        assert len(result.context) == len(result.sources)

    def test_recommendation_deep_read_respects_config_cap(self, monkeypatch):
        async def fake_rewrite(user_query, prior_clarification=None, company_name=None):
            return {
                "queries": ["best books about startups"],
                "entities": [],
                "time_sensitive": False,
            }

        monkeypatch.setattr(
            web_search_mod, "rewrite_search_queries", fake_rewrite
        )

        async def fake_snippets(client, query_item, settings, time_sensitive):
            return [
                (
                    f"Book list result {i} with some excerpt text",
                    f"https://example.com/books/{i}",
                    "DuckDuckGo",
                    None,
                    None,
                )
                for i in range(20)
            ]

        extract_calls: list[str] = []

        async def fake_extract(client, url, query, settings):
            extract_calls.append(url)
            return (
                f"Full content from {url}: detailed book titles and authors.",
                {
                    "title": f"Extracted: {url[:80]}",
                    "url": url,
                    "provider": "Tavily Extract",
                },
            )

        monkeypatch.setattr(web_search_mod, "_snippets_for_query", fake_snippets)
        monkeypatch.setattr(web_search_mod, "_tavily_extract", fake_extract)
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", "test-key"
        )
        monkeypatch.setattr(
            web_search_mod.get_settings(), "MAX_DEEP_READ_URLS", 5
        )

        result = asyncio.run(web_search_mod.search_web("best books about startups"))
        assert len(extract_calls) == 5
        # Deep-read page bodies lead the evidence pool, still source-aligned.
        assert result.context[0].startswith("Full content from https://example.com/books/0")
        assert result.sources[0]["provider"] == "Tavily Extract"
        assert len(result.context) == len(result.sources)
