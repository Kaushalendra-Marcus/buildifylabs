"""Accuracy layers for live-web answers (items 1-7).

All provider I/O is faked at the helper seam - no HTTP here. Covers:
- concurrent Tavily+DDG merge (both run, either may fail alone)
- recency: published_date carried into sources + date-tagged prompt
- time-sensitive flag (LLM + regex backstop)
- fundamentals + FRED structured adapters (best-effort, never raise)
- grounding validation (invented numbers dropped, cited kept)
- new visual shapes (timeline / comparison / outlook status)
- repeat-query cache (second identical call reuses evidence)
- stronger-model routing for live-web narration
"""
import asyncio
import json

import app.services.llm.langchain_pipeline as pipeline_mod
import app.services.web_search as web_search_mod
import app.services.web_search_cache as cache_mod
from app.services.llm.langchain_pipeline import (
    VisualOutput,
    _comparison_from_figures,
    _figures_from_snippets,
    _fundamentals_comparison_visual,
    _outlook_status_visual,
    _timeline_visual,
    _visual_numbers_grounded,
    build_prompt,
    drop_ungrounded_visuals,
)
from app.services.llm.query_rewriter import (
    _coerce_rewrite_payload,
    is_time_sensitive_query,
)


class TestTimeSensitiveFlag:
    def test_regex_catches_recency_phrasing(self):
        assert is_time_sensitive_query("latest Tesla news this week")
        assert is_time_sensitive_query("current inflation rate?")
        assert not is_time_sensitive_query("explain market cap vs revenue")

    def test_llm_flag_survives_coercion(self):
        framed = _coerce_rewrite_payload(
            {"queries": ["a"], "entities": ["Tesla"], "time_sensitive": True},
            "raw",
        )
        assert framed["time_sensitive"] is True

    def test_missing_flag_falls_back_to_regex(self):
        assert (
            _coerce_rewrite_payload({"queries": ["a"]}, "latest news on X")[
                "time_sensitive"
            ]
            is True
        )
        assert (
            _coerce_rewrite_payload({"queries": ["a"]}, "plain question")[
                "time_sensitive"
            ]
            is False
        )


class TestDateTagging:
    def test_build_prompt_tags_published_date(self):
        prompt = build_prompt(
            "latest news?",
            [],
            {},
            news_context=["Acme raised $5M"],
            source_scope="live_web",
            web_sources=[
                {
                    "title": "t",
                    "url": "u",
                    "provider": "Tavily",
                    "published_date": "2024-11-02",
                }
            ],
        )
        assert "[1, 2024-11-02]" in prompt

    def test_missing_date_falls_back_to_plain_number(self):
        prompt = build_prompt(
            "q",
            [],
            {},
            news_context=["snippet"],
            source_scope="live_web",
            web_sources=[{"title": "t", "url": "u", "provider": "X"}],
        )
        assert "[1]" in prompt


class TestGrounding:
    SNIPPETS = ["Acme raised $300k in 2024", "Globex sold for $1M"]

    def _graph(self, values):
        return VisualOutput(
            visual_type="graph",
            props={
                "chart_type": "bar",
                "labels": ["a", "b"],
                "datasets": [{"name": "amount", "values": values}],
            },
            title="g",
        )

    def test_cited_numbers_ground(self):
        assert _visual_numbers_grounded(
            self._graph([300000.0, 1000000.0]), self.SNIPPETS
        )

    def test_invented_magnitude_does_not_ground(self):
        assert not _visual_numbers_grounded(
            self._graph([42000000.0]), self.SNIPPETS
        )

    def test_qualitative_cards_need_no_numbers(self):
        table = VisualOutput(
            visual_type="table",
            props={"columns": ["Source"], "values": [["Acme raises"]]},
            title="Sources cited",
        )
        assert _visual_numbers_grounded(table, self.SNIPPETS)

    def test_drop_keeps_grounded_discards_rest(self):
        kept, dropped = drop_ungrounded_visuals(
            [self._graph([300000.0]), self._graph([42000000.0])],
            self.SNIPPETS,
        )
        assert len(kept) == 1 and dropped == 1


class TestNewVisualShapes:
    def test_timeline_needs_two_dated_snippets(self):
        timeline = _timeline_visual(
            ["first event happened", "second event happened"],
            [{"published_date": "2024-11-02"}, {"published_date": "2024-11-05"}],
        )
        assert timeline is not None
        assert timeline.title == "Timeline"
        assert timeline.props["columns"] == ["Date", "Event"]
        assert _timeline_visual(["only one"], [{"published_date": "2024-11-02"}]) is None
        assert _timeline_visual(["a", "b"], [{}, {}]) is None

    def test_comparison_fires_only_on_comparative_intent(self):
        figures = _figures_from_snippets(
            ["Acme raised $300k", "Globex raised $1M"]
        )
        comparison = _comparison_from_figures(figures, "compare Acme vs Globex")
        assert comparison is not None
        assert comparison.visual_type == "comparison"
        assert comparison.props["value"] == 300000.0
        assert _comparison_from_figures(figures, "tell me about funding") is None

    def test_outlook_status_maps_sentiment(self):
        positive = _outlook_status_visual(
            ["profits surged to record gains, strong growth beat"],
            "what is the outlook, should i buy?",
        )
        assert positive is not None
        assert positive.props["state"] == "on_track"
        negative = _outlook_status_visual(
            ["losses crashed, layoffs amid fraud lawsuit"],
            "what is the outlook?",
        )
        assert negative is not None
        assert negative.props["state"] == "off_track"
        assert (
            _outlook_status_visual(["some snippet"], "plain factual question") is None
        )

    def test_fundamentals_comparison_from_structured_values(self):
        comparison = _fundamentals_comparison_visual(
            [
                {"entity": "A", "symbol": "A", "market_cap": 1e9},
                {"entity": "B", "symbol": "B", "market_cap": 2e9},
            ]
        )
        assert comparison is not None
        assert comparison.visual_type == "comparison"
        assert _fundamentals_comparison_visual(
            [{"entity": "A", "symbol": "A", "market_cap": 1e9}]
        ) is None


class TestConcurrentProvidersAndCache:
    def test_both_providers_run_and_merge(self, monkeypatch):
        cache_mod._reset_cache_state()

        async def fake_tavily(client, query_item, settings, time_sensitive=False):
            return [
                ("tavily result", "https://t.example", "Tavily", "2024-11-02", 0.9)
            ]

        async def fake_ddg(client, query_item, settings):
            return [("ddg result", "https://d.example", "DuckDuckGo", None, None)]

        async def fake_rewrite(query, prior_clarification=None, company_name=None):
            return {
                "queries": ["latest tesla news"],
                "entities": [],
                "time_sensitive": True,
            }

        monkeypatch.setattr(web_search_mod, "_tavily_search", fake_tavily)
        monkeypatch.setattr(web_search_mod, "_ddg_search", fake_ddg)
        monkeypatch.setattr(
            web_search_mod, "rewrite_search_queries", fake_rewrite
        )
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", "test-key"
        )

        result = asyncio.run(web_search_mod.search_web("latest tesla news"))
        providers = {source["provider"] for source in result.sources}
        assert providers == {"Tavily", "DuckDuckGo"}
        assert any(
            source.get("published_date") == "2024-11-02"
            for source in result.sources
        )

    def test_repeat_query_hits_cache(self, monkeypatch):
        cache_mod._reset_cache_state()
        calls = {"n": 0}

        async def fake_tavily(client, query_item, settings, time_sensitive=False):
            calls["n"] += 1
            return [("tavily result", "https://t.example", "Tavily", None, None)]

        async def fake_ddg(client, query_item, settings):
            calls["n"] += 1
            return [("ddg result", "https://d.example", "DuckDuckGo", None, None)]

        async def fake_rewrite(query, prior_clarification=None, company_name=None):
            return {"queries": ["same question"], "entities": [], "time_sensitive": False}

        monkeypatch.setattr(web_search_mod, "_tavily_search", fake_tavily)
        monkeypatch.setattr(web_search_mod, "_ddg_search", fake_ddg)
        monkeypatch.setattr(web_search_mod, "rewrite_search_queries", fake_rewrite)
        monkeypatch.setattr(
            web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", "test-key"
        )

        first = asyncio.run(web_search_mod.search_web("same question"))
        second = asyncio.run(web_search_mod.search_web("same question"))
        assert first.context == second.context
        assert calls["n"] == 2  # one Tavily + one DDG on the first call only


class TestStrongModelRouting:
    def test_live_web_uses_strong_model(self, monkeypatch):
        from app.config import get_settings

        get_settings().GROQ_STRONG_MODEL = "strong-model-id"
        seen = {}

        async def fake_generate(prompt, system_prompt, temperature=0.2,
                                max_tokens=512, **kwargs):
            seen["model"] = kwargs.get("model")
            if "decision" in system_prompt.lower() or "decision step" in system_prompt.lower():
                return {
                    "content": json.dumps(
                        {
                            "decision": "answer",
                            "missing": "",
                            "chart_from_prior": False,
                            "visual_plan": [],
                            "suggested_options": [],
                        }
                    ),
                    "source": "groq",
                    "usage": None,
                }
            return {
                "content": json.dumps(
                    {
                        "answer": "hi",
                        "visuals": [],
                        "insights": [],
                        "summary": "",
                        "root_causes": [],
                        "recommendations": [],
                        "news_context": [],
                        "anomalies": [],
                        "confidence": 0.5,
                        "clarification": None,
                    }
                ),
                "source": "groq",
                "usage": None,
            }

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)
        try:
            asyncio.run(
                pipeline_mod.run_pipeline(
                    user_query="latest news?",
                    db_data=[],
                    source_scope="live_web",
                    news_context=["snippet one"],
                )
            )
            assert seen["model"] == "strong-model-id"
            asyncio.run(
                pipeline_mod.run_pipeline(
                    user_query="my revenue?",
                    db_data=[{"revenue": 1}],
                    source_scope="own_data",
                )
            )
            assert seen["model"] is None
        finally:
            get_settings().GROQ_STRONG_MODEL = None


class TestJsonTransportCircuitBreaker:
    """Live incident: model openai/gpt-oss-safeguard-20b 400s on
    response_format=json_object AND returns empty completions on the plain
    retry. Without a breaker every structured call burns 3 attempts x keys +
    backoffs before the inevitable fallback."""

    def _install_kwarg_aware(self, monkeypatch):
        import app.services.llm.groq_service as groq_mod
        from types import SimpleNamespace

        calls: list[dict] = []

        class _Err400(Exception):
            status_code = 400

        class _Completions:
            async def create(self, **kwargs):
                calls.append(kwargs)
                if kwargs.get("response_format") == {"type": "json_object"}:
                    raise _Err400("json_validate_failed")
                message = SimpleNamespace(content='{"queries": ["q"], "entities": []}')
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        class _Client:
            def __init__(self, api_key):
                self.chat = SimpleNamespace(completions=_Completions())

        monkeypatch.setattr(groq_mod, "AsyncGroq", _Client)
        monkeypatch.setattr(
            groq_mod,
            "settings",
            SimpleNamespace(groq_api_keys=["k1"], GROQ_MODEL="m"),
        )

        async def _no_hf(prompt, system_prompt):
            raise AssertionError("HF fallback must not be reached")

        monkeypatch.setattr(groq_mod, "hf_fallback", _no_hf)
        groq_mod._reset_key_state()
        return groq_mod, calls

    def test_second_call_skips_doomed_json_transport(self, monkeypatch):
        groq_mod, calls = self._install_kwarg_aware(monkeypatch)

        first = asyncio.run(
            groq_mod.generate_response(
                prompt="p", system_prompt="Return JSON.", json_mode=True
            )
        )
        assert json.loads(first["content"])["queries"] == ["q"]
        # First call: JSON attempt (400) + plain retry (200).
        assert calls[0].get("response_format") == {"type": "json_object"}
        assert "response_format" not in calls[1]

        second = asyncio.run(
            groq_mod.generate_response(
                prompt="p", system_prompt="Return JSON.", json_mode=True
            )
        )
        assert json.loads(second["content"])["queries"] == ["q"]
        # Breaker tripped: single plain call, no response_format.
        assert "response_format" not in calls[2]
        assert len(calls) == 3


class TestJudgeTruncationRetry:
    """Live incident: judge JSON truncated mid-string at max_tokens=400
    (suggested_options cut off) -> fail-open 'answer', losing the verdict."""

    def test_truncated_then_valid_recovers_verdict(self, monkeypatch):
        from app.services.llm.langchain_pipeline import judge_sufficiency

        truncated = (
            '{"decision": "clarify", "missing": "", "chart_from_prior": false, '
            '"visual_plan": [], "suggested_options": ["Yahoo Finance daily '
            "closing prices for Tesla"
        )
        valid = json.dumps(
            {
                "decision": "clarify",
                "missing": "price data source",
                "chart_from_prior": False,
                "visual_plan": [],
                "suggested_options": ["daily closes", "weekly averages"],
            }
        )
        replies = [truncated, valid]

        async def fake_generate(prompt, system_prompt, temperature=0.0,
                                max_tokens=400, **kwargs):
            assert max_tokens >= 800, "judge budget must cover option lists"
            return {"content": replies.pop(0), "source": "groq", "usage": None}

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        decision = asyncio.run(
            judge_sufficiency(
                user_query="compare Tesla and Reliance?",
                source_scope="live_web",
                evidence={"row_count": 0, "web_snippet_count": 0},
            )
        )
        assert decision.decision == "clarify"
        assert decision.missing == "price data source"


class TestEntityFallback:
    """Live incident: rewrite outage -> entities=[] -> Yahoo never queried
    for an explicit 'Tesla and Reliance stock' question -> honest-but-wrong
    'could not locate daily closing price data'."""

    def test_aliases_recovered_from_raw_text(self):
        from app.services.web_search import _fallback_entities_from_text

        assert _fallback_entities_from_text(
            "Compare Tesla and Reliance stock performance over the last month"
        ) == ["Tesla", "Reliance"]

    def test_chat_glue_and_stopwords_yield_no_junk(self):
        from app.services.web_search import _fallback_entities_from_text

        assert _fallback_entities_from_text("Weekly average price, Percentage change") == []

    def test_market_data_survives_rewrite_outage(self, monkeypatch):
        cache_mod._reset_cache_state()

        async def fake_rewrite(query, prior_clarification=None, company_name=None):
            return {"queries": [query], "entities": [], "time_sensitive": False}

        async def fake_resolve(client, entity):
            return {"Tesla": "TSLA", "Reliance": "RELIANCE.NS"}.get(entity)

        async def fake_series(client, entity, symbol):
            return (
                f"{entity} text",
                {"entity": entity, "symbol": symbol, "labels": ["a"], "values": [1.0, 2.0]},
                {"title": entity, "url": "", "provider": "Yahoo Finance"},
            )

        async def fake_ddg(client, query_item, settings):
            return []

        monkeypatch.setattr(web_search_mod, "rewrite_search_queries", fake_rewrite)
        monkeypatch.setattr(web_search_mod, "_resolve_symbol", fake_resolve)
        monkeypatch.setattr(web_search_mod, "_fetch_market_series", fake_series)
        monkeypatch.setattr(web_search_mod, "_ddg_search", fake_ddg)

        result = asyncio.run(
            web_search_mod.search_web(
                "Compare Tesla and Reliance stock performance over the last month"
            )
        )
        assert {series["symbol"] for series in result.market_data} == {
            "TSLA",
            "RELIANCE.NS",
        }


class TestDdgChallenge:
    """Live incident: DDG answered 202 + challenge page; parser fed on it and
    returned zero pairs - silent empty evidence."""

    def test_non_200_raises_provider_failure(self, monkeypatch):
        from types import SimpleNamespace

        async def fake_get(url, headers=None):
            return SimpleNamespace(
                status_code=202,
                text="<html>challenge</html>",
                raise_for_status=lambda: None,
            )

        client = SimpleNamespace(get=fake_get)
        try:
            asyncio.run(
                web_search_mod._ddg_search(
                    client, "q", SimpleNamespace(WEB_SEARCH_MAX_RESULTS=5)
                )
            )
        except RuntimeError as exc:
            assert "202" in str(exc)
        else:
            raise AssertionError("DDG 202 must raise, not parse challenge HTML")
