"""Query framing + web fan-out (generic, no per-topic rules).

Covers: the LLM rewriter returning clean queries/entities, soft failure back
to the raw query, and the merge/dedupe fan-out across framed queries.
HTTP is never touched here - snippet providers are faked at the helper seam.
"""
import asyncio

import app.services.llm.query_rewriter as rewriter_mod
import app.services.web_search as web_search_mod
from app.services.llm.query_rewriter import rewrite_search_queries
from app.services.web_search import _dedupe_sources, _dedupe_texts, search_web


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

    def test_query_list_capped_at_three(self, monkeypatch):
        import json

        monkeypatch.setattr(
            rewriter_mod,
            "generate_response",
            _rewrite_fake(json.dumps({"queries": ["a", "b", "c", "d", "e"]})),
        )

        framed = asyncio.run(rewrite_search_queries("q"))
        assert framed["queries"] == ["a", "b", "c"]

    def test_garbage_falls_back_to_raw_query(self, monkeypatch):
        monkeypatch.setattr(
            rewriter_mod, "generate_response", _rewrite_fake("not json at all")
        )

        framed = asyncio.run(rewrite_search_queries("raw question?"))
        assert framed == {"queries": ["raw question?"], "entities": []}

    def test_empty_reply_falls_back_to_raw_query(self, monkeypatch):
        monkeypatch.setattr(rewriter_mod, "generate_response", _rewrite_fake("   "))

        framed = asyncio.run(rewrite_search_queries("raw question?"))
        assert framed == {"queries": ["raw question?"], "entities": []}

    def test_transport_failure_falls_back_to_raw_query(self, monkeypatch):
        monkeypatch.setattr(
            rewriter_mod,
            "generate_response",
            _rewrite_fake(exc=RuntimeError("down")),
        )

        framed = asyncio.run(rewrite_search_queries("raw question?"))
        assert framed == {"queries": ["raw question?"], "entities": []}


class TestFanOutMerge:
    def test_dedupe_texts_case_and_space_insensitive(self):
        assert _dedupe_texts(["Acme raised $5M", "  acme RAISED  $5m ", "Other"]) == [
            "Acme raised $5M",
            "Other",
        ]

    def test_dedupe_sources_by_url(self):
        sources = [
            {"title": "A", "url": "https://a.example", "provider": "X"},
            {"title": "A2", "url": "https://a.example", "provider": "Y"},
            {"title": "B", "url": "https://b.example", "provider": "X"},
        ]
        assert [s["title"] for s in _dedupe_sources(sources)] == ["A", "B"]

    def test_framed_queries_all_run_and_merge(self, monkeypatch):
        import json

        monkeypatch.setattr(
            rewriter_mod,
            "generate_response",
            _rewrite_fake(
                json.dumps({"queries": ["first angle", "second angle"]})
            ),
        )

        async def fake_ddg(client, query_item, settings):
            return (
                [f"result for {query_item}", "shared result"],
                [
                    {
                        "title": query_item,
                        "url": f"https://{query_item.replace(' ', '-')}.example",
                        "provider": "DuckDuckGo",
                    }
                ],
            )

        monkeypatch.setattr(web_search_mod, "_ddg_search", fake_ddg)
        # Force the DDG path (no Tavily key in test env).
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None
        )

        result = asyncio.run(search_web("messy raw question?"))
        assert "result for first angle" in result.context
        assert "result for second angle" in result.context
        # Shared hit merged once; both per-query sources kept.
        assert result.context.count("shared result") == 1
        assert len(result.sources) == 2
