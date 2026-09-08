"""Final correctness-hardening pass (fail-closed validation, TimeRange words,
metric synonyms, canonical plan_query semantics, adversarial pipeline tests).

All adversarial tests run through the ACTUAL pipeline (run_pipeline), not
just helpers. No new planner/engine/model; deterministic validation stays
authoritative over the LLM.
"""

import asyncio
import datetime
import json

import pytest

import app.services.llm.langchain_pipeline as pipeline_mod
from app.services import web_search_cache
from app.services.data.comparison import (
    METRIC_PROFIT,
    METRIC_REVENUE,
    METRIC_STOCK,
    detect_metrics,
    detect_period_years,
    parse_time_range,
)
from app.services.llm.langchain_pipeline import (
    PipelineOutput,
    _fail_closed_gate,
    drop_ungrounded_visuals,
    ensure_visuals,
    run_pipeline,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    web_search_cache._reset_cache_state()
    yield
    web_search_cache._reset_cache_state()


def _sequenced_fake(*contents):
    calls = {"n": 0}

    async def fake(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
        content = contents[min(calls["n"], len(contents) - 1)]
        calls["n"] += 1
        if isinstance(content, Exception):
            raise content
        if isinstance(content, str):
            return {"content": content, "source": "groq", "usage": None}
        return {"content": json.dumps(content), "source": "groq", "usage": None}

    return fake


def _decision_json(**overrides):
    default = {
        "decision": "answer", "missing": "", "chart_from_prior": False,
        "visual_plan": [], "suggested_options": [],
    }
    default.update(overrides)
    return default


def _pipeline_json(**overrides):
    default = {
        "answer": "Narrated answer.", "visuals": [], "insights": [],
        "summary": "", "root_causes": [], "recommendations": [],
        "news_context": [], "anomalies": [], "confidence": 0.7,
        "clarification": None,
    }
    default.update(overrides)
    return default


def _graph_visual(title="LLM graph"):
    return {
        "visual_type": "graph",
        "props": {
            "chart_type": "line",
            "labels": ["2022-01-01", "2023-01-01"],
            "datasets": [{"name": "NVIDIA", "values": [20.0, 180.0]}],
        },
        "title": title,
    }


def _comparison_visual(title="LLM comparison"):
    return {
        "visual_type": "comparison",
        "props": {
            "value": 800.0, "baseline": 100.0,
            "groups": [
                {"label": "NVIDIA", "value": 800.0},
                {"label": "AMD", "value": 100.0},
            ],
        },
        "title": title,
    }


def _three_year_weekly(entity="NVIDIA", symbol="NVDA", start=20.0, end=180.0, n=40):
    base = datetime.date(2022, 9, 7)
    labels, values = [], []
    for i in range(n):
        day = base + datetime.timedelta(days=int(i * (3 * 365 / n)))
        labels.append(day.isoformat())
        values.append(round(start + (end - start) * i / (n - 1), 2))
    return {
        "entity": entity, "symbol": symbol, "currency": "USD",
        "labels": labels, "values": values, "range": "3y",
        "interval": "1wk", "frequency": "weekly",
        "period_start": labels[0], "period_end": labels[-1],
        "is_historical": True, "metric": "close",
    }


def _financial(entity, symbol, labels, rev, inc, currency=None):
    out = {"entity": entity, "symbol": symbol, "frequency": "annual", "is_historical": True}
    out["revenue"] = {
        "labels": labels, "values": rev, "period_start": labels[0],
        "period_end": labels[-1], "metric": "annualTotalRevenue",
        "unit": currency or "currency",
    }
    out["net_income"] = {
        "labels": labels, "values": inc, "period_start": labels[0],
        "period_end": labels[-1], "metric": "annualNetIncome",
        "unit": currency or "currency",
    }
    if currency:
        out["revenue"]["currency"] = currency
        out["net_income"]["currency"] = currency
        out["currency"] = currency
    return out


NVIDIA_AMD_3Y = (
    "Compare NVIDIA and AMD over the last 3 years. Compare revenue "
    "growth, profitability, and stock performance."
)
TESLA_3Y = (
    "Compare Tesla, BYD, and Toyota over the last 3 years. Which company had "
    "the highest revenue growth, strongest stock price performance, and best "
    "profitability?"
)
STARTUP_Q = "which is best to start tech startup or robotics startup, with statistics"


def _full_nvidia_amd_evidence():
    price = [
        _three_year_weekly("NVIDIA", "NVDA", 20.0, 180.0),
        _three_year_weekly("AMD", "AMD", 80.0, 160.0),
    ]
    financial = [
        _financial("NVIDIA", "NVDA", ["2022", "2023", "2024"],
                   [27e9, 27e9, 60e9], [9e9, 4e9, 29e9]),
        _financial("AMD", "AMD", ["2022", "2023", "2024"],
                   [23e9, 22e9, 25e9], [1.3e9, 0.8e9, 1.6e9]),
    ]
    return price, financial


def _sources(n=2):
    return [
        {"title": f"s{i}", "url": f"https://a.example/{i}", "provider": "Yahoo Finance"}
        for i in range(n)
    ]


def _kinds(output):
    return [v.visual_type for v in (output.visuals or [])]


# ---------------------------------------------------------------------------
# 1. Fail-closed validation
# ---------------------------------------------------------------------------
class TestFailClosed:
    def test_gate_exception_blocks_historical_chart_end_to_end(self, monkeypatch):
        """Gate throws -> BLOCKED, confidence 0, no graph/comparison/bar."""
        def _boom(*args, **kwargs):
            raise RuntimeError("yahoo exploded")

        monkeypatch.setattr(pipeline_mod, "_historical_comparison_gate", _boom)
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(answer="3Y comparison.", confidence=0.85,
                               visuals=[_graph_visual(), _comparison_visual()]),
            ),
        )
        price, financial = _full_nvidia_amd_evidence()
        output = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_3Y, db_data=[], source_scope="live_web",
            news_context=["NVIDIA snippet", "AMD snippet"],
            web_sources=_sources(),
            price_history=price, financial_history=financial,
        ))
        assert output.confidence == 0.0
        assert "graph" not in _kinds(output)
        assert "comparison" not in _kinds(output)

    def test_ensure_visuals_gate_exception_strips_existing_charts(self, monkeypatch):
        """ensure_visuals + throwing gate + confidence 0 -> no graph/comparison."""
        def _boom(*args, **kwargs):
            raise RuntimeError("gate down")

        monkeypatch.setattr(pipeline_mod, "_historical_comparison_gate", _boom)
        output = PipelineOutput(**_pipeline_json(
            confidence=0.0, visuals=[_graph_visual(), _comparison_visual()]))
        gated = ensure_visuals(
            output, rows=[], computed_numbers={}, market_data=[],
            web_sources=_sources(1), news_context=["snippet"],
            preferred_visual=None, query=NVIDIA_AMD_3Y,
            fundamentals=[], macro_data=[],
            price_history=[], financial_history=[],
        )
        assert "graph" not in _kinds(gated)
        assert "comparison" not in _kinds(gated)

    def test_completeness_exception_blocks_charts(self, monkeypatch):
        """Completeness throws on a would-be PASSED gate -> no graph/comparison."""
        def _boom(plan, evidence):
            raise RuntimeError("completeness down")

        monkeypatch.setattr(pipeline_mod, "check_research_completeness", _boom)
        output = PipelineOutput(**_pipeline_json(confidence=0.8, visuals=[]))
        price, financial = _full_nvidia_amd_evidence()
        gated = ensure_visuals(
            output, rows=[], computed_numbers={}, market_data=[],
            web_sources=_sources(1),
            news_context=["NVIDIA snippet", "AMD snippet"],
            preferred_visual=None, query=NVIDIA_AMD_3Y,
            fundamentals=[], macro_data=[],
            price_history=price, financial_history=financial,
        )
        assert "graph" not in _kinds(gated)
        assert "comparison" not in _kinds(gated)

    def test_grounding_exception_drops_visual(self, monkeypatch):
        """Grounding check throws -> visual dropped, never kept unverified."""

        def _boom(visual, snippets):
            raise RuntimeError("grounding down")

        monkeypatch.setattr(pipeline_mod, "_visual_numbers_grounded", _boom)
        kept, dropped = drop_ungrounded_visuals([_graph_visual()], ["snippet"])
        assert kept == [] and dropped == 1

    def test_fail_closed_gate_helper_marks_comparison_blocked(self):
        gate = _fail_closed_gate(NVIDIA_AMD_3Y, "boom")
        assert gate["applies"] is True
        assert gate["blocked"] is True
        assert gate["comparison_stats"] is None
        assert "boom" in gate["blocked_reason"]


# ---------------------------------------------------------------------------
# 2. TimeRange parsing: one through ten
# ---------------------------------------------------------------------------
class TestTimeRangeWords:
    @pytest.mark.parametrize(("text", "years"), [
        ("Compare NVIDIA and AMD over the last eight years on revenue growth", 8),
        ("Compare NVIDIA and AMD over the last nine years on revenue growth", 9),
        ("Compare NVIDIA and AMD over the last 10 years on revenue growth", 10),
        ("Compare NVIDIA and AMD 10y revenue growth", 10),
        ("Compare NVIDIA and AMD over the last seven years on revenue growth", 7),
        ("Compare NVIDIA and AMD 7y revenue growth", 7),
    ])
    def test_relative_windows(self, text, years):
        assert detect_period_years(text) == years
        assert parse_time_range(text).years == years

    def test_ten_year_bare_form(self):
        assert detect_period_years("Compare NVIDIA and AMD over 10 years on revenue") == 10


# ---------------------------------------------------------------------------
# 3. Deterministic metric synonyms
# ---------------------------------------------------------------------------
class TestMetricSynonyms:
    def test_top_line_is_revenue(self):
        assert METRIC_REVENUE in detect_metrics(
            "Compare Tesla and BYD top-line over the last 3 years")

    def test_bottom_line_is_profit(self):
        assert METRIC_PROFIT in detect_metrics(
            "Compare Tesla and BYD bottom-line over the last 3 years")

    def test_earnings_is_profitability(self):
        assert METRIC_PROFIT in detect_metrics(
            "Compare Tesla and BYD earnings over the last 3 years")

    def test_returns_is_stock_performance(self):
        assert METRIC_STOCK in detect_metrics(
            "Compare Tesla and BYD returns over the last 3 years")

    def test_bare_growth_triggers_no_metric(self):
        assert detect_metrics("Compare Tesla and BYD growth") == []

    def test_bare_performance_triggers_no_metric(self):
        assert detect_metrics("Compare Tesla and BYD performance") == []


# ---------------------------------------------------------------------------
# 4. Canonical plan_query semantics (conflicting period)
# ---------------------------------------------------------------------------
class TestCanonicalQuery:
    def test_conflicting_period_uses_canonical_plan_end_to_end(self, monkeypatch):
        """Fragment says 1Y, canonical plan says 3Y: research, gate, and
        narration must all follow the canonical 3Y ResearchPlan."""
        prompts: list = []

        async def fake(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {"content": json.dumps(_decision_json()),
                        "source": "groq", "usage": None}
            return {"content": json.dumps(_pipeline_json(
                answer="3Y NVIDIA vs AMD.", confidence=0.8)),
                "source": "groq", "usage": None}

        monkeypatch.setattr(pipeline_mod, "generate_response", fake)
        price, financial = _full_nvidia_amd_evidence()
        output = asyncio.run(run_pipeline(
            user_query="just the last 1 year please",
            db_data=[], source_scope="live_web",
            news_context=["NVIDIA snippet", "AMD snippet"],
            web_sources=_sources(),
            price_history=price, financial_history=financial,
            plan_query=NVIDIA_AMD_3Y,
        ))
        # Canonical plan wins: 3Y research, PASSED gate, charted.
        assert output.research_state["plan"]["entities"] == ["NVIDIA", "AMD"]
        assert output.research_state["plan"]["time_range"]["years"] == 3
        assert output.confidence > 0.0
        assert "graph" in _kinds(output)
        # Narration saw the canonical query, not the bare fragment.
        narration_prompts = prompts[1:]
        assert narration_prompts
        assert any("NVIDIA" in prompt for prompt in narration_prompts)


# ---------------------------------------------------------------------------
# 5. Adversarial integration through the ACTUAL pipeline
# ---------------------------------------------------------------------------
class TestAdversarialPipeline:
    def test_llm_graph_despite_blocked_gate_stripped(self, monkeypatch):
        """Partial evidence (AMD missing) + LLM ships graph -> stripped."""
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(answer="Both did great.", confidence=0.75,
                               visuals=[_graph_visual("AMD vs NVIDIA chart")]),
            ),
        )
        price = [_three_year_weekly("NVIDIA", "NVDA", 20.0, 180.0)]
        output = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_3Y, db_data=[], source_scope="live_web",
            news_context=["NVIDIA snippet"],
            web_sources=_sources(1),
            price_history=price, financial_history=[],
        ))
        assert output.confidence == 0.0
        assert "graph" not in _kinds(output)
        assert "comparison" not in _kinds(output)

    def test_llm_invented_number_removed(self, monkeypatch):
        """PASSED gate + LLM chart with a number from nowhere -> removed."""
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    answer="Huge numbers.",
                    confidence=0.8,
                    visuals=[{
                        "visual_type": "graph",
                        "props": {
                            "chart_type": "bar",
                            "labels": ["NVIDIA"],
                            "datasets": [{"name": "x", "values": [999999999.0]}],
                        },
                        "title": "invented",
                    }],
                ),
            ),
        )
        price, financial = _full_nvidia_amd_evidence()
        output = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_3Y, db_data=[], source_scope="live_web",
            news_context=["NVIDIA revenue $60B in 2024", "AMD revenue $25B in 2024"],
            web_sources=_sources(),
            price_history=price, financial_history=financial,
        ))

        def _walk(node):
            if isinstance(node, bool):
                return
            if isinstance(node, (int, float)):
                yield float(node)
            elif isinstance(node, dict):
                for value in node.values():
                    yield from _walk(value)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    yield from _walk(value)

        for visual in (output.visuals or []):
            assert 999999999.0 not in list(_walk(visual.props))

    def test_judge_clarify_for_public_stats_overridden(self, monkeypatch):
        """Judge asks the user for public revenue figures -> answered anyway."""
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(decision="clarify",
                               missing="Please provide annual revenue figures"),
                _pipeline_json(answer="Researched all three.", confidence=0.75),
            ),
        )
        price = [
            _three_year_weekly("Tesla", "TSLA", 100.0, 250.0),
            _three_year_weekly("BYD", "BYDDY", 30.0, 60.0),
            _three_year_weekly("Toyota", "TM", 150.0, 200.0),
        ]
        financial = [
            _financial("Tesla", "TSLA", ["2022", "2023", "2024"],
                       [90e9, 96e9, 100e9], [12e9, 15e9, 16e9]),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024"],
                       [400e9, 500e9, 600e9], [15e9, 25e9, 30e9]),
            _financial("Toyota", "TM", ["2022", "2023", "2024"],
                       [30e12, 35e12, 40e12], [2e12, 3e12, 3.5e12]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=TESLA_3Y, db_data=[], source_scope="live_web",
            news_context=["Tesla snippet", "BYD snippet", "Toyota snippet"],
            web_sources=_sources(3),
            price_history=price, financial_history=financial,
        ))
        assert output.clarification is None
        assert output.confidence > 0.0

    def test_funding_vs_cost_never_compares_end_to_end(self, monkeypatch):
        """Startup query with funding + startup-cost snippets -> no
        comparison visual and no 504999900% artifact."""
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(answer="Funding and launch costs differ.",
                               confidence=0.6),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query=STARTUP_Q, db_data=[], source_scope="live_web",
            news_context=[
                "Tech startups raised $202B in AI funding in 2024",
                "Robotics startup launch startup cost was $40K in 2024",
            ],
            web_sources=_sources(),
        ))
        assert "comparison" not in _kinds(output)
        assert "504999900" not in (output.answer or "")

    def test_two_of_three_companies_partially_charts_validated_subset(self, monkeypatch):
        """Toyota financials missing -> PARTIAL: validated Tesla/BYD subset
        charts with explicit Toyota exclusion (never zero-filled, never silent)."""
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(answer="Tesla vs BYD vs Toyota.", confidence=0.8,
                               visuals=[_comparison_visual("Tesla vs BYD vs Toyota")]),
            ),
        )
        price = [
            _three_year_weekly("Tesla", "TSLA", 100.0, 250.0),
            _three_year_weekly("BYD", "BYDDY", 30.0, 60.0),
            _three_year_weekly("Toyota", "TM", 150.0, 200.0),
        ]
        financial = [
            _financial("Tesla", "TSLA", ["2022", "2023", "2024"],
                       [90e9, 96e9, 100e9], [12e9, 15e9, 16e9]),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024"],
                       [400e9, 500e9, 600e9], [15e9, 25e9, 30e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=TESLA_3Y, db_data=[], source_scope="live_web",
            news_context=["Tesla snippet", "BYD snippet", "Toyota snippet"],
            web_sources=_sources(3),
            price_history=price, financial_history=financial,
        ))
        # Partial-result policy: sufficient subset answers, excluded named.
        assert output.confidence > 0.0
        assert output.confidence <= 0.65
        assert "toyota" in (output.answer or "").lower()
        # Validated subset may chart (stock history has all three; financial
        # tables cover Tesla/BYD only) -- but never a fabricated Toyota value.
        def _walk(node):
            if isinstance(node, bool):
                return
            if isinstance(node, (int, float)):
                yield float(node)
            elif isinstance(node, dict):
                for value in node.values():
                    yield from _walk(value)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    yield from _walk(value)
        # No zero-fill for the missing entity.
        for visual in (output.visuals or []):
            assert 0.0 not in list(_walk(visual.props)) or visual.visual_type == "table"
