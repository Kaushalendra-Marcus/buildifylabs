"""Long-source summarization (specs/07 hardening, Phase 4).

Covers evidence_summarizer map-reduce (cost-control, joined summaries,
failure fallback, NOTHING_RELEVANT fallback) and the _tavily_extract
integration (over-threshold content summarizes instead of raw slicing).
"""
import asyncio

import app.services.llm.evidence_summarizer as summarizer_mod
import app.services.web_search as web_search_mod
from app.services.llm.evidence_summarizer import (
    SUMMARIZE_CHUNK_CHARS,
    SUMMARIZE_MAX_CHUNKS,
    summarize_long_text,
)


def _raising_fake(*args, **kwargs):
    async def fake(**_kwargs):
        raise AssertionError("generate_response must not be called for short text")

    return fake


class TestSummarizeLongText:
    def test_short_text_never_calls_llm(self, monkeypatch):
        calls = []

        async def fake(**kwargs):
            calls.append(1)
            raise AssertionError("should not be called")

        monkeypatch.setattr(summarizer_mod, "generate_response", fake)
        text = "short source " * 20
        assert len(text) <= SUMMARIZE_CHUNK_CHARS
        result = asyncio.run(summarize_long_text(text, "query"))
        assert result == text
        assert calls == []

    def test_long_text_joins_chunk_summaries_with_call_cap(self, monkeypatch):
        calls = []

        async def fake(prompt, system_prompt, model=None, temperature=0.0, max_tokens=250, **kwargs):
            calls.append(1)
            return {"content": "summary bit", "source": "groq", "usage": None}

        monkeypatch.setattr(summarizer_mod, "generate_response", fake)
        text = "x" * 10000
        result = asyncio.run(summarize_long_text(text, "query"))
        assert "summary bit" in result
        assert result != text[: SUMMARIZE_CHUNK_CHARS * SUMMARIZE_MAX_CHUNKS]
        assert len(calls) <= SUMMARIZE_MAX_CHUNKS
        assert len(calls) >= 2

    def test_llm_failure_falls_back_to_truncation(self, monkeypatch):
        async def fake(**kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(summarizer_mod, "generate_response", fake)
        text = "y" * 10000
        result = asyncio.run(summarize_long_text(text, "query"))
        assert result == text[: SUMMARIZE_CHUNK_CHARS * SUMMARIZE_MAX_CHUNKS]

    def test_nothing_relevant_falls_back_to_truncation(self, monkeypatch):
        async def fake(**kwargs):
            return {"content": "NOTHING_RELEVANT", "source": "groq", "usage": None}

        monkeypatch.setattr(summarizer_mod, "generate_response", fake)
        text = "z" * 10000
        result = asyncio.run(summarize_long_text(text, "query"))
        assert result == text[: SUMMARIZE_CHUNK_CHARS * SUMMARIZE_MAX_CHUNKS]
        assert result != ""


class TestTavilyExtractIntegration:
    def test_over_threshold_content_calls_summarizer(self, monkeypatch):
        from app.services.llm import evidence_summarizer as summ_mod

        calls = {}

        async def fake_summarize(content, query):
            calls["content_len"] = len(content)
            calls["query"] = query
            return "SUMMARIZED"

        monkeypatch.setattr(summ_mod, "summarize_long_text", fake_summarize)
        # web_search imports summarize_long_text lazily inside the function,
        # so patching the source module attribute is sufficient.
        monkeypatch.setattr(
            web_search_mod.get_settings(), "SUMMARIZE_TRIGGER_CHARS", 6000
        )
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", "k"
        )

        big_content = "w" * 8000

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [{"raw_content": big_content}]}

        class FakeClient:
            async def post(self, *args, **kwargs):
                return FakeResponse()

        text, source = asyncio.run(
            web_search_mod._tavily_extract(
                FakeClient(), "https://example.com/a", "query",
                web_search_mod.get_settings(),
            )
        )
        assert calls.get("content_len") == 8000
        assert "SUMMARIZED" in text
        assert source["url"] == "https://example.com/a"
