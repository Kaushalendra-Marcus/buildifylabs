"""Query framing + web fan-out (generic, no per-topic rules).

Covers: the LLM rewriter returning clean queries/entities, soft failure back
to the raw query, and the merge/dedupe fan-out across framed queries.
HTTP is never touched here - snippet providers are faked at the helper seam.
"""
import asyncio

import app.services.llm.query_rewriter as rewriter_mod
import app.services.web_search as web_search_mod
from app.services.llm.query_rewriter import rewrite_search_queries
from app.services.llm.query_rewriter import wants_external_context
from app.services.web_search import _dedupe_texts, search_web


def _rewrite_fake(content=None, exc=None):
    async def fake(prompt, system_prompt, temperature=0.0, max_tokens=300, **kwargs):
        if exc is not None:
            raise exc
        return {"content": content, "source": "groq", "usage": None}

    return fake


class TestRewriteSearchQueries:
    def test_valid_rewrite_returns_queries_and_entities(self, monkeypatch):
        import json

        monkeypatch.setattr(
            rewriter_mod,
            "generate_response",
            _rewrite_fake(
                json.dumps(
                    {
                        "queries": [
                            "startups under $100M funding 2025",
                            "startups under $100M funding site:reddit.com",
                        ],
                        "entities": ["OpenAI"],
                    }
                )
            ),
        )

        framed = asyncio.run(
            rewrite_search_queries("best startups with less than 100M funding?")
        )
        assert framed["queries"] == [
            "startups under $100M funding 2025",
            "startups under $100M funding site:reddit.com",
        ]
        assert framed["entities"] == ["OpenAI"]

    def test_query_list_capped_at_four(self, monkeypatch):
        import json

        monkeypatch.setattr(
            rewriter_mod,
            "generate_response",
            _rewrite_fake(json.dumps({"queries": ["a", "b", "c", "d", "e"]})),
        )

        framed = asyncio.run(rewrite_search_queries("q"))
        assert framed["queries"] == ["a", "b", "c", "d"]

    def test_garbage_falls_back_to_raw_query(self, monkeypatch):
        monkeypatch.setattr(
            rewriter_mod, "generate_response", _rewrite_fake("not json at all")
        )

        framed = asyncio.run(rewrite_search_queries("raw question?"))
        assert framed["queries"] == ["raw question?"]
        assert framed["entities"] == []
        assert framed["time_sensitive"] is False

    def test_empty_reply_falls_back_to_raw_query(self, monkeypatch):
        monkeypatch.setattr(rewriter_mod, "generate_response", _rewrite_fake("   "))

        framed = asyncio.run(rewrite_search_queries("raw question?"))
        assert framed["queries"] == ["raw question?"]
        assert framed["entities"] == []
        assert framed["time_sensitive"] is False

    def test_transport_failure_falls_back_to_raw_query(self, monkeypatch):
        monkeypatch.setattr(
            rewriter_mod,
            "generate_response",
            _rewrite_fake(exc=RuntimeError("down")),
        )

        framed = asyncio.run(rewrite_search_queries("raw question?"))
        assert framed["queries"] == ["raw question?"]
        assert framed["entities"] == []
        assert framed["time_sensitive"] is False


class TestFanOutMerge:
    def test_dedupe_texts_case_and_space_insensitive(self):
        assert _dedupe_texts(["Acme raised $5M", "  acme RAISED  $5m ", "Other"]) == [
            "Acme raised $5M",
            "Other",
        ]

    def test_framed_queries_all_run_and_merge(self, monkeypatch):
        import json

        monkeypatch.setattr(
            rewriter_mod,
            "generate_response",
            _rewrite_fake(
                json.dumps({"queries": ["first angle", "second angle"]})
            ),
        )

        async def fake_firecrawl(client, query_item, settings, time_sensitive=False):
            return [
                (f"result for {query_item}", f"https://{query_item.replace(' ', '-')}.example", "Firecrawl"),
                ("shared result", "", "Firecrawl"),
            ]

        monkeypatch.setattr(web_search_mod, "_firecrawl_search", fake_firecrawl)
        # Force the Firecrawl path (no Tavily key in test env).
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None
        )
        monkeypatch.setattr(
            web_search_mod.get_settings(), "FIRECRAWL_API_KEY", "test-key"
        )

        result = asyncio.run(search_web("messy raw question?"))
        assert "result for first angle" in result.context
        assert "result for second angle" in result.context
        # Shared hit merged once; both per-query sources kept.
        assert result.context.count("shared result") == 1
        assert len(result.sources) == 3
        # Alignment: citation [n] always resolves to sources[n-1].
        assert len(result.context) == len(result.sources)
        assert result.sources[0]["url"] == "https://first-angle.example"
        assert result.sources[1] == {
            "title": "shared result",
            "url": "",
            "provider": "Firecrawl",
        }


class TestRewriteSearchQueriesPriorQuery:
    def test_prior_query_reaches_the_prompt_only_when_passed(self, monkeypatch):
        import json

        seen = {}

        async def fake(prompt, system_prompt, temperature=0.0, max_tokens=300, **kwargs):
            seen["prompt"] = prompt
            return {
                "content": json.dumps({"queries": ["q1"]}),
                "source": "groq",
                "usage": None,
            }

        monkeypatch.setattr(rewriter_mod, "generate_response", fake)

        framed = asyncio.run(
            rewrite_search_queries("what about last month", prior_query="revenue by region?")
        )
        assert framed["queries"] == ["q1"]
        assert "revenue by region?" in seen["prompt"]
        assert "Previous question in this conversation" in seen["prompt"]

        seen.clear()
        framed = asyncio.run(rewrite_search_queries("what about last month"))
        assert framed["queries"] == ["q1"]
        assert "Previous question in this conversation" not in seen["prompt"]


class TestWantsExternalContext:
    def test_positive_cases(self):
        assert wants_external_context("check the news on X") is True
        assert wants_external_context("what's happening in the market today") is True
        assert wants_external_context("search the web for Y") is True

    def test_negative_cases(self):
        assert wants_external_context("what was my march revenue") is False
        assert wants_external_context("show me a bar chart") is False
