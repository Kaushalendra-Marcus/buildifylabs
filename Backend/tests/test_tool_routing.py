"""Judge-directed tool routing: the planner picks evidence adapters from the
tool catalog, dispatch honors the plan, and any planner failure falls back
to the deterministic intent predicates (never narrows, never raises).
"""
import asyncio
import json

import pytest

import app.services.llm.langchain_pipeline as pipeline_mod
import app.services.web_search as web_search_mod
from app.services import web_search_cache
from app.services.llm.langchain_pipeline import (
    Decision,
    normalize_decision_payload,
    normalize_tool_plan,
    plan_tools,
)
from app.services.web_search import search_web


@pytest.fixture(autouse=True)
def _reset_shared_state():
    web_search_cache._reset_cache_state()
    yield
    web_search_cache._reset_cache_state()


def _plan_fake(content=None, exc=None):
    calls: dict = {}

    async def fake(prompt, system_prompt, temperature=0.0, max_tokens=200, **kwargs):
        calls["prompt"] = prompt
        calls["system_prompt"] = system_prompt
        if exc is not None:
            raise exc
        return {"content": content, "source": "groq", "usage": None}

    return fake, calls


class TestNormalizeToolPlan:
    def test_keeps_catalog_keys_in_order_deduped(self):
        assert normalize_tool_plan(["market", "bogus", "market", "WIKIpedia "]) == [
            "market",
            "wikipedia",
        ]

    def test_non_list_is_empty(self):
        assert normalize_tool_plan(None) == []
        assert normalize_tool_plan("snippets") == []
        assert normalize_tool_plan({"tools_needed": ["snippets"]}) == []

    def test_decision_model_accepts_tools_needed(self):
        decision = Decision(decision="answer", tools_needed=["macro", "nope"])
        assert decision.tools_needed == ["macro", "nope"]

    def test_decision_payload_normalizes_tools(self):
        normalized = normalize_decision_payload(
            {"decision": "answer", "tools_needed": ["snippets", "bogus"]}
        )
        assert normalized["tools_needed"] == ["snippets"]

    def test_decision_payload_without_tools_defaults_empty(self):
        normalized = normalize_decision_payload({"decision": "answer"})
        assert normalized["tools_needed"] == []


class TestPlanTools:
    def test_valid_plan_returned(self, monkeypatch):
        fake, calls = _plan_fake(json.dumps({"tools_needed": ["market", "snippets"]}))
        monkeypatch.setattr(pipeline_mod, "generate_response", fake)
        planned = asyncio.run(plan_tools("Tesla stock price?"))
        assert planned == ["market", "snippets"]
        assert "wikipedia" in calls["system_prompt"]
        assert "extract" in calls["system_prompt"]

    def test_unknown_names_dropped(self, monkeypatch):
        fake, _ = _plan_fake(json.dumps({"tools_needed": ["bogus"]}))
        monkeypatch.setattr(pipeline_mod, "generate_response", fake)
        assert asyncio.run(plan_tools("q")) == []

    def test_garbage_reply_falls_back_to_empty(self, monkeypatch):
        fake, _ = _plan_fake("not json at all")
        monkeypatch.setattr(pipeline_mod, "generate_response", fake)
        assert asyncio.run(plan_tools("q")) == []

    def test_transport_failure_falls_back_to_empty(self, monkeypatch):
        fake, _ = _plan_fake(exc=RuntimeError("down"))
        monkeypatch.setattr(pipeline_mod, "generate_response", fake)
        assert asyncio.run(plan_tools("q")) == []

    def test_empty_reply_twice_falls_back_to_empty(self, monkeypatch):
        fake, _ = _plan_fake("   ")
        monkeypatch.setattr(pipeline_mod, "generate_response", fake)
        assert asyncio.run(plan_tools("q")) == []


class TestPlannedDispatch:
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

    def _base_monkeypatch(self, monkeypatch, queries, entities):
        monkeypatch.setattr(
            web_search_mod, "rewrite_search_queries", self._rewrite_fake(queries, entities)
        )
        monkeypatch.setattr(web_search_mod, "_ddg_search", self._no_snippets)
        monkeypatch.setattr(web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None)
        monkeypatch.setattr(web_search_mod.get_settings(), "FRED_API_KEY", None)

    def test_plan_limits_to_wikipedia_only(self, monkeypatch):
        """A wikipedia-only plan for a stock-price question keeps the LLM's
        wikipedia request but the canonical plan restores the
        deterministically required market tool: the planner may ADD tools,
        never silently REMOVE required ones (Phase 2 contract)."""
        calls: list = []
        self._base_monkeypatch(monkeypatch, ["q1"], ["Tesla"])

        async def fake_symbol(client, entity):
            calls.append(("symbol", entity))
            return "TSLA"

        async def fake_market(client, entity, symbol):
            calls.append(("market", entity))
            return (
                "Tesla (TSLA) one-month market data.",
                {"entity": entity, "symbol": symbol, "values": [1.0, 2.0]},
                {"title": "t", "url": "u", "provider": "Yahoo Finance"},
            )

        async def fake_wiki(client, entity):
            calls.append(("wiki", entity))
            return (
                "Tesla: a company (Wikipedia summary.)",
                {"title": "Wikipedia: Tesla", "url": "https://en.wikipedia.org/wiki/Tesla", "provider": "Wikipedia"},
            )

        monkeypatch.setattr(web_search_mod, "_resolve_symbol", fake_symbol)
        monkeypatch.setattr(web_search_mod, "_fetch_market_series", fake_market)
        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", fake_wiki)
        result = asyncio.run(
            search_web("Tesla stock price?", planned_tools=["wikipedia"])
        )
        assert ("wiki", "Tesla") in calls
        # Canonical plan restored the required market tool alongside wiki.
        assert ("market", "Tesla") in calls
        assert any(text.startswith("Tesla:") for text in result.context)

    def test_planner_cannot_drop_required_financial_history(self, monkeypatch):
        """Canonical regression for the Phase 2 example: an LLM plan of only
        ["market_history"] for a multi-metric historical comparison must gain
        financial_history deterministically."""
        from app.services.data.comparison import resolve_tool_plan

        query = (
            "Compare NVIDIA and AMD over the last 3 years. Compare revenue "
            "growth, profitability, and stock performance."
        )
        resolved = resolve_tool_plan(["market_history"], query)
        assert "market_history" in resolved
        assert "financial_history" in resolved
        assert "snippets" in resolved

    def test_empty_plan_falls_back_to_predicates(self, monkeypatch):
        """planned=[] is planner-abstain: deterministic dispatch runs."""
        calls: list = []
        self._base_monkeypatch(monkeypatch, ["q1"], ["Tesla"])
        monkeypatch.setattr(
            web_search_mod, "_resolve_symbol", lambda client, entity: asyncio.sleep(0, result="TSLA")
        )

        async def fake_market(client, entity, symbol):
            calls.append("market")
            return (
                "Tesla (TSLA) one-month market data.",
                {"entity": entity, "symbol": symbol, "values": [1.0, 2.0]},
                {"title": "t", "url": "u", "provider": "Yahoo Finance"},
            )

        monkeypatch.setattr(web_search_mod, "_fetch_market_series", fake_market)

        async def fake_wiki(client, entity):
            calls.append("wiki")
            return ("Tesla wiki.", {"title": "t", "url": "u", "provider": "Wikipedia"})

        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", fake_wiki)
        result = asyncio.run(search_web("Tesla stock price?", planned_tools=[]))
        assert "market" in calls and "wiki" in calls
        assert result.market_data and result.context

    def test_unknown_names_fall_back_to_predicates(self, monkeypatch):
        self._base_monkeypatch(monkeypatch, ["q1"], ["Acme"])

        async def fake_wiki(client, entity):
            return ("Acme wiki.", {"title": "t", "url": "u", "provider": "Wikipedia"})

        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", fake_wiki)
        result = asyncio.run(search_web("Who is Acme?", planned_tools=["bogus"]))
        assert result.context[0] == "Acme wiki."

    def test_plan_without_snippets_skips_providers(self, monkeypatch):
        self._base_monkeypatch(monkeypatch, ["q1"], ["Tesla"])
        monkeypatch.setattr(
            web_search_mod, "_resolve_symbol", lambda client, entity: asyncio.sleep(0, result="TSLA")
        )

        async def boom_ddg(client, query_item, settings):
            raise AssertionError("snippet providers must not run without 'snippets'")

        monkeypatch.setattr(web_search_mod, "_ddg_search", boom_ddg)

        async def fake_market(client, entity, symbol):
            return (
                "Tesla (TSLA) one-month market data.",
                {"entity": entity, "symbol": symbol, "values": [1.0, 2.0]},
                {"title": "t", "url": "u", "provider": "Yahoo Finance"},
            )

        monkeypatch.setattr(web_search_mod, "_fetch_market_series", fake_market)

        async def fake_wiki(client, entity):
            raise AssertionError("wiki must not run without 'wikipedia'")

        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", fake_wiki)
        result = asyncio.run(search_web("Tesla stock price?", planned_tools=["market"]))
        assert len(result.market_data) == 1
        assert result.context and result.sources

    def test_cache_key_varies_with_plan(self):
        from app.services.web_search_cache import make_cache_key

        base = make_cache_key(["q"], ["Tesla"], None, False)
        planned = make_cache_key(["q"], ["Tesla"], None, False, planned_tools=["market"])
        assert base != planned
        assert make_cache_key(["q"], ["Tesla"], None, False) == base
