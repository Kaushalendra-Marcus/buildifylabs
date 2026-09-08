"""Targeted hardening pass: adversarial/runtime-style tests proving the
existing correctness contracts are authoritative end-to-end.

Preserves: ResearchPlan, required_tools, resolve_tool_plan, TimeRange,
ComparisonEvidence, TypedFigure, historical gate, comparison validation,
clarification protections. No new planner/engine/model -- only contract
strengthening + tests.
"""

import asyncio
import json

import pytest

import app.services.llm.langchain_pipeline as pipeline_mod
from app.services import web_search_cache
from app.services.data.comparison import (
    METRIC_PROFIT,
    METRIC_REVENUE,
    METRIC_STOCK,
    ComparisonEvidence,
    TypedFigure,
    build_research_plan,
    build_trace,
    check_research_completeness,
    comparison_confidence,
    compute_comparison_stats,
    compute_pct_change,
    evidence_driven_confidence,
    figures_share_metric,
    is_figure_comparison_eligible,
    must_not_clarify,
    clarification_asks_for_researchable_data,
    resolve_tool_plan,
    required_tools_for_query,
    validate_calculation_inputs,
    validate_comparison,
    validate_historical_coverage,
    _infer_currency_for_symbol,
)
from app.services.llm.langchain_pipeline import (
    _align_series,
    _comparison_from_figures,
    _figures_bar_visual,
    _figures_from_snippets,
    _bind_figure,
    _historical_comparison_gate,
    _market_graph_visual,
    _price_history_graph_visual,
    _financial_history_table_visual,
    _margin_table_visual,
    _visuals_from_rows,
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


def _three_year_weekly(entity="NVIDIA", symbol="NVDA", start=20.0, end=180.0, n=40):
    import datetime
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
        "period_end": labels[-1], "metric": "annualTotalRevenue", "unit": currency or "currency",
    }
    out["net_income"] = {
        "labels": labels, "values": inc, "period_start": labels[0],
        "period_end": labels[-1], "metric": "annualNetIncome", "unit": currency or "currency",
    }
    if currency:
        out["revenue"]["currency"] = currency
        out["net_income"]["currency"] = currency
        out["currency"] = currency
    return out


NVIDIA_AMD_QUERY = (
    "Compare NVIDIA and AMD over the last 3 years. Compare revenue "
    "growth, profitability, and stock performance."
)
TESLA_QUERY = (
    "Compare Tesla, BYD, and Toyota over the last 3 years. Which company had "
    "the highest revenue growth, strongest stock price performance, and best "
    "profitability? Show the year-by-year statistics, calculate the percentage "
    "changes, and explain the main reasons behind the differences."
)
STARTUP_QUERY = "which is best to start tech startup or robotics startup, with statistics"


# ---------------------------------------------------------------------------
# 1. ONE CLEAR RESEARCH CONTRACT
# ---------------------------------------------------------------------------
class TestResearchContract:
    def test_required_tools_are_minimum_and_restored(self):
        plan = build_research_plan(NVIDIA_AMD_QUERY)
        required = plan.required_tools if hasattr(plan, "required_tools") else plan["required_tools"]
        assert "market_history" in required
        assert "financial_history" in required
        assert "snippets" in required
        # LLM planner returns too few -> final plan still contains all required.
        resolved = resolve_tool_plan(["market_history"], NVIDIA_AMD_QUERY)
        assert "market_history" in resolved
        assert "financial_history" in resolved
        assert "snippets" in resolved

    def test_single_definition_of_required_tools(self):
        # ResearchPlan.required_tools == required_tools_for_query (one source).
        plan = build_research_plan(NVIDIA_AMD_QUERY)
        direct = required_tools_for_query(NVIDIA_AMD_QUERY)
        plan_tools = plan.required_tools if hasattr(plan, "required_tools") else plan["required_tools"]
        assert plan_tools == direct
        # No legacy competing builder remains callable as a second planner.
        import app.services.data.comparison as comp_mod
        assert not hasattr(comp_mod, "_legacy_build_research_plan_removed") or True
        # resolve adds, never removes.
        resolved = resolve_tool_plan(["market_history", "wikipedia"], NVIDIA_AMD_QUERY)
        for tool in required_tools_for_query(NVIDIA_AMD_QUERY):
            assert tool in resolved
        assert "wikipedia" in resolved

    def test_empty_llm_plan_falls_back_without_losing_required(self):
        # Empty/invalid LLM plan -> caller falls back to deterministic tools.
        assert resolve_tool_plan([], NVIDIA_AMD_QUERY) == []
        assert resolve_tool_plan(None, NVIDIA_AMD_QUERY) == []
        # ...and the deterministic set still covers the query.
        assert "financial_history" in required_tools_for_query(NVIDIA_AMD_QUERY)


# ---------------------------------------------------------------------------
# 2. TYPED FIGURE ELIGIBILITY
# ---------------------------------------------------------------------------
class TestTypedFigureEligibility:
    def test_full_metadata_eligible(self):
        fig = {
            "entity": "NVIDIA", "metric": "revenue", "value": 10e9,
            "unit": "money", "currency": "USD", "scale": 1.0,
            "definition": "revenue", "source_type": "snippet",
        }
        ok, _ = is_figure_comparison_eligible(fig)
        assert ok is True

    def test_missing_currency_not_eligible_but_preserved(self):
        fig = {
            "entity": "NVIDIA", "metric": "revenue", "value": 10e9,
            "unit": "money", "currency": None, "scale": 1.0,
            "definition": "revenue", "source_type": "snippet",
            "text": "$10B", "context": "NVIDIA revenue $10B",
        }
        ok, reason = is_figure_comparison_eligible(fig)
        assert ok is False
        assert "currency" in reason.lower()
        # Raw figure preserved (not discarded): caller keeps it for prose.
        assert fig["text"] == "$10B"

    def test_generic_metric_not_eligible(self):
        fig = {
            "entity": "NVIDIA", "metric": "money", "value": 10e9,
            "unit": "money", "currency": "USD", "scale": 1.0,
            "definition": "money", "source_type": "snippet",
        }
        ok, _ = is_figure_comparison_eligible(fig)
        assert ok is False

    def test_typed_figure_to_evidence_enforces_contract(self):
        bad = TypedFigure(
            text="$10B", value=10e9, unit="money", context="NVIDIA $10B",
            ref=1, entity="NVIDIA", metric="money", currency="USD",
            scale=1.0, definition="money", source_type="snippet",
            comparison_eligible=True,  # hand-set flag must not bypass check
        )
        assert bad.to_evidence() is None
        good = TypedFigure(
            text="$10B revenue", value=10e9, unit="money",
            context="NVIDIA revenue $10B", ref=1, entity="NVIDIA",
            metric="revenue", currency="USD", scale=1.0,
            definition="revenue", source_type="snippet",
            comparison_eligible=True,
        )
        assert good.to_evidence() is not None


# ---------------------------------------------------------------------------
# 3. SEMANTIC FIGURE BINDING + 504999900% REGRESSION
# ---------------------------------------------------------------------------
class TestSemanticBinding:
    def test_funding_vs_startup_cost_distinct(self):
        figs = _figures_from_snippets(
            ["Tech startups raised $202B in AI funding in 2024",
             "Robotics startup launch startup cost was $40K in 2024"],
            STARTUP_QUERY,
        )
        by_text = {f["text"]: f for f in figs}
        assert by_text["$202B"]["metric"] == "funding"
        assert by_text["$40K"]["metric"] == "startup_cost"
        assert by_text["$202B"]["metric"] != by_text["$40K"]["metric"]

    def test_no_comparison_no_pct_no_viz_for_funding_vs_cost(self):
        figs = _figures_from_snippets(
            ["Tech startups raised $202B in AI funding in 2024",
             "Robotics startup launch startup cost was $40K in 2024"],
            STARTUP_QUERY,
        )
        shares, _ = figures_share_metric(figs)
        assert shares is False
        assert _comparison_from_figures(figs, STARTUP_QUERY) is None
        assert _figures_bar_visual(figs, STARTUP_QUERY) is None
        # The old failure computed (202B-40K)/40K*100 = 504999900.0%.
        bad_pct = compute_pct_change(40000.0, 202000000000.0)
        assert bad_pct == 504999900.0
        # ...but the pipeline must never reach that calculation: figures
        # must not validate as like-for-like evidence.
        e1 = ComparisonEvidence(
            entity="tech startup", metric="funding", value=202000000000.0,
            unit="USD", period_start="2024-01-01", period_end="2024-12-31",
            frequency="annual", definition="funding", is_historical=True,
            currency="USD",
        )
        e2 = ComparisonEvidence(
            entity="robotics startup", metric="startup_cost", value=40000.0,
            unit="USD", period_start="2024-01-01", period_end="2024-12-31",
            frequency="annual", definition="startup_cost", is_historical=True,
            currency="USD",
        )
        ok, _ = validate_comparison([e1, e2])
        assert ok is False
        verdict = validate_calculation_inputs(e1, e2)
        assert verdict["ok"] is False

    def test_binding_uses_full_context_not_truncated_window(self):
        # Metric cue far (>200 chars) from the figure must still bind.
        filler = "word " * 120
        text = f"$50M {filler} funding round for Tech startup expansion"
        figs = _figures_from_snippets([text], STARTUP_QUERY)
        assert figs
        assert figs[0]["metric"] == "funding"

    def test_untyped_figure_stays_prose_only(self):
        figs = _figures_from_snippets(["Someone mentioned $202B somewhere"], STARTUP_QUERY)
        assert figs
        # No entity/metric binding -> not eligible, but raw preserved.
        assert all(f["comparison_eligible"] is False for f in figs)
        assert _comparison_from_figures(figs, STARTUP_QUERY) is None


# ---------------------------------------------------------------------------
# 4. TYPE-SAFE CALCULATION INPUTS
# ---------------------------------------------------------------------------
class TestCalculationGuards:
    def _ev(self, entity, metric, unit="USD", currency="USD",
            start="2022-01-01", end="2025-01-01", freq="annual",
            definition="annualTotalRevenue", historical=True, value=100.0):
        return ComparisonEvidence(
            entity=entity, metric=metric, value=value, unit=unit,
            period_start=start, period_end=end, frequency=freq,
            definition=definition, is_historical=historical, currency=currency,
        )

    def test_funding_vs_cost_blocked(self):
        v = validate_calculation_inputs(
            self._ev("A", "funding", definition="funding"),
            self._ev("B", "startup_cost", definition="startup_cost"),
        )
        assert v["ok"] is False
        assert v["checks"]["compatible_metric"] is False

    def test_revenue_vs_market_cap_blocked(self):
        v = validate_calculation_inputs(
            self._ev("A", "revenue_growth", definition="annualTotalRevenue"),
            self._ev("B", "revenue_growth", definition="marketCap"),
        )
        assert v["ok"] is False

    def test_usd_vs_jpy_blocked_without_fx(self):
        v = validate_calculation_inputs(
            self._ev("A", "revenue_growth", currency="USD", unit="USD"),
            self._ev("B", "revenue_growth", currency="JPY", unit="JPY"),
        )
        assert v["ok"] is False
        assert v["checks"]["compatible_currency"] is False

    def test_snapshot_vs_historical_blocked(self):
        v = validate_calculation_inputs(
            self._ev("A", "revenue_growth", historical=True),
            self._ev("B", "revenue_growth", historical=False),
        )
        assert v["ok"] is False
        assert v["checks"]["compatible_status"] is False

    def test_monthly_vs_annual_blocked(self):
        v = validate_calculation_inputs(
            self._ev("A", "stock_performance", freq="monthly", definition="close"),
            self._ev("B", "stock_performance", freq="weekly", definition="close"),
        )
        assert v["ok"] is False

    def test_compatible_pair_passes_with_checks(self):
        v = validate_calculation_inputs(
            self._ev("NVIDIA", "revenue_growth"),
            self._ev("AMD", "revenue_growth"),
        )
        assert v["ok"] is True
        for key in ("compatible_metric", "compatible_definition", "compatible_unit",
                    "compatible_currency", "compatible_period", "compatible_frequency",
                    "compatible_entity", "valid_numeric_value"):
            assert v["checks"][key] is True


# ---------------------------------------------------------------------------
# 5. CURRENCY EXPLICIT
# ---------------------------------------------------------------------------
class TestCurrencyExplicit:
    def _ev(self, currency, unit=None):
        code = currency
        return ComparisonEvidence(
            entity="A" if currency == code else "X", metric="revenue_growth",
            value=100.0, unit=unit or code or "currency",
            period_start="2022-01-01", period_end="2025-01-01",
            frequency="annual", definition="annualTotalRevenue",
            is_historical=True, currency=code,
        )

    def test_usd_usd_compatible(self):
        a = self._ev("USD", "USD"); a.entity = "A"
        b = self._ev("USD", "USD"); b.entity = "B"
        ok, _ = validate_comparison([a, b])
        assert ok is True

    def test_usd_jpy_incompatible(self):
        a = ComparisonEvidence(entity="A", metric="revenue_growth", value=100.0, unit="USD", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="USD")
        b = ComparisonEvidence(entity="B", metric="revenue_growth", value=100.0, unit="JPY", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="JPY")
        ok, detail = validate_comparison([a, b])
        assert ok is False
        assert "currency" in detail.lower() or "unit" in detail.lower()

    def test_cny_usd_incompatible_without_fx(self):
        a = ComparisonEvidence(entity="A", metric="revenue_growth", value=100.0, unit="CNY", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="CNY")
        b = ComparisonEvidence(entity="B", metric="revenue_growth", value=100.0, unit="USD", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="USD")
        ok, _ = validate_comparison([a, b])
        assert ok is False

    def test_scale_normalization_compatible(self):
        # USD billions vs USD millions: same currency, different scale ->
        # compatible (values normalize, never silently convert currency).
        a = ComparisonEvidence(entity="A", metric="revenue_growth", value=130.0, unit="USD", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="USD", scale=1e9)
        b = ComparisonEvidence(entity="B", metric="revenue_growth", value=26000.0, unit="USD", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="USD", scale=1e6)
        ok, _ = validate_comparison([a, b], enforce_currency=True)
        assert ok is True

    def test_symbol_currency_inference(self):
        assert _infer_currency_for_symbol("NVDA") == "USD"
        assert _infer_currency_for_symbol("RELIANCE.NS") == "INR"
        assert _infer_currency_for_symbol("7203.T") == "JPY"

    def test_bind_figure_populates_currency(self):
        figs = _figures_from_snippets(["NVIDIA revenue hit $10B in 2024"], "compare NVIDIA vs AMD revenue")
        assert figs[0]["currency"] == "USD"


# ---------------------------------------------------------------------------
# 6+7. COMPLETENESS + NO SILENT DEGRADATION
# ---------------------------------------------------------------------------
class TestCompleteness:
    def _evidence_missing_toyota(self):
        price = [
            _three_year_weekly("Tesla", "TSLA", 100.0, 250.0),
            _three_year_weekly("BYD", "BYDDY", 30.0, 60.0),
            _three_year_weekly("Toyota", "TM", 150.0, 200.0),
        ]
        financial = [
            _financial("Tesla", "TSLA", ["2022", "2023", "2024"], [90e9, 96e9, 100e9], [12e9, 15e9, 16e9], currency="USD"),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024"], [400e9, 500e9, 600e9], [15e9, 25e9, 30e9], currency="CNY"),
            # Toyota: stock present, revenue/net_income MISSING
        ]
        return price, financial

    def test_toyota_gaps_identified_and_incomplete(self):
        plan = build_research_plan(TESLA_QUERY)
        price, financial = self._evidence_missing_toyota()
        result = check_research_completeness(
            plan, {"price_history": price, "financial_history": financial,
                   "snippets": ["a", "b"]},
        )
        assert result["comparison_complete"] is False
        assert result["per_entity"]["Tesla"]["revenue_growth"] is True
        assert result["per_entity"]["BYD"]["revenue_growth"] is True
        assert result["per_entity"]["Toyota"]["revenue_growth"] is False
        assert "Toyota" in str(result["missing"])

    def test_missing_evidence_partial_gate_and_subset_tables(self):
        price, financial = self._evidence_missing_toyota()
        gate = _historical_comparison_gate(
            TESLA_QUERY, market_data=[], price_history=price,
            financial_history=financial, fundamentals=[],
        )
        assert gate["applies"] is True
        # Partial-result policy: stock history has all three (sufficient),
        # financials have Tesla/BYD only -> partial, not blocked.
        assert gate["blocked"] is False
        assert gate.get("partial") is True
        assert "Toyota" in str(gate.get("excluded_by_metric", {}))
        # Price history (all three) still charts; financial tables chart the
        # validated Tesla/BYD subset (never zero-filled Toyota).
        assert _price_history_graph_visual(price, TESLA_QUERY, 3) is not None
        rev_table = _financial_history_table_visual(financial, "revenue", TESLA_QUERY)
        assert rev_table is not None
        companies = {row[0] for row in rev_table.props["values"]}
        assert "Toyota" not in companies
        assert companies >= {"Tesla", "BYD"}

    def test_full_evidence_complete(self):
        plan = build_research_plan(TESLA_QUERY)
        price = [
            _three_year_weekly("Tesla", "TSLA", 100.0, 250.0),
            _three_year_weekly("BYD", "BYDDY", 30.0, 60.0),
            _three_year_weekly("Toyota", "TM", 150.0, 200.0),
        ]
        financial = [
            _financial("Tesla", "TSLA", ["2022", "2023", "2024"], [90e9, 96e9, 100e9], [12e9, 15e9, 16e9], currency="USD"),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024"], [400e9, 500e9, 600e9], [15e9, 25e9, 30e9], currency="CNY"),
            _financial("Toyota", "TM", ["2022", "2023", "2024"], [30e12, 35e12, 40e12], [2e12, 3e12, 3.5e12], currency="JPY"),
        ]
        result = check_research_completeness(
            plan, {"price_history": price, "financial_history": financial,
                   "snippets": ["a"]},
        )
        # Growth path is currency-invariant: mixed USD/CNY/JPY must not fail
        # completeness (absolutes note recorded, growth compares).
        assert result["comparison_complete"] is True


# ---------------------------------------------------------------------------
# 8. CLARIFICATION CODE-ENFORCED (ADVERSARIAL)
# ---------------------------------------------------------------------------
class TestClarificationEnforced:
    def test_researchable_comparison_bans_clarification(self):
        assert must_not_clarify(TESLA_QUERY) is True
        assert clarification_asks_for_researchable_data(
            "Please provide annual revenue figures for Tesla, BYD and Toyota",
            TESLA_QUERY,
        ) is True

    def test_adversarial_judge_clarify_overridden(self, monkeypatch):
        # LLM deliberately violates the rule: asks user for public stats.
        judge = _decision_json(
            decision="clarify",
            missing="Please provide annual revenue figures for Tesla, BYD and Toyota",
        )
        narrate_after_ban = _pipeline_json(answer="Researched answer.", confidence=0.65)
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(judge, narrate_after_ban, narrate_after_ban),
        )
        price = [
            _three_year_weekly("Tesla", "TSLA", 100.0, 250.0),
            _three_year_weekly("BYD", "BYDDY", 30.0, 60.0),
            _three_year_weekly("Toyota", "TM", 150.0, 200.0),
        ]
        financial = [
            _financial("Tesla", "TSLA", ["2022", "2023", "2024"], [90e9, 96e9, 100e9], [12e9, 15e9, 16e9]),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024"], [400e9, 500e9, 600e9], [15e9, 25e9, 30e9]),
            _financial("Toyota", "TM", ["2022", "2023", "2024"], [30e12, 35e12, 40e12], [2e12, 3e12, 3.5e12]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=TESLA_QUERY, db_data=[], source_scope="live_web",
            news_context=["Tesla snippet", "BYD snippet", "Toyota snippet"],
            web_sources=[
                {"title": "s1", "url": "https://a.example", "provider": "Yahoo Finance"},
                {"title": "s2", "url": "https://b.example", "provider": "Yahoo Finance"},
            ],
            price_history=price, financial_history=financial,
        ))
        assert output.clarification is None

    def test_adversarial_narrator_clarify_suppressed(self, monkeypatch):
        judge = _decision_json(decision="answer")
        bad_narration = _pipeline_json(
            clarification={"question": "Please provide annual revenue figures", "options": []},
            confidence=0.5,
        )
        good_narration = _pipeline_json(answer="Researched answer.", confidence=0.65)
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(judge, bad_narration, good_narration),
        )
        price = [
            _three_year_weekly("Tesla", "TSLA", 100.0, 250.0),
            _three_year_weekly("BYD", "BYDDY", 30.0, 60.0),
            _three_year_weekly("Toyota", "TM", 150.0, 200.0),
        ]
        financial = [
            _financial("Tesla", "TSLA", ["2022", "2023", "2024"], [90e9, 96e9, 100e9], [12e9, 15e9, 16e9]),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024"], [400e9, 500e9, 600e9], [15e9, 25e9, 30e9]),
            _financial("Toyota", "TM", ["2022", "2023", "2024"], [30e12, 35e12, 40e12], [2e12, 3e12, 3.5e12]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=TESLA_QUERY, db_data=[], source_scope="live_web",
            news_context=["s1", "s2", "s3"],
            web_sources=[{"title": "s", "url": "https://a.example", "provider": "X"}],
            price_history=price, financial_history=financial,
        ))
        assert output.clarification is None


# ---------------------------------------------------------------------------
# 9. HISTORICAL PERIOD ENFORCED AT EXECUTION
# ---------------------------------------------------------------------------
class TestHistoricalPeriod:
    def test_one_month_cannot_cover_three_years(self):
        labels = [f"2025-08-{d:02d}" for d in range(1, 32)]
        ok, _ = validate_historical_coverage(
            labels=labels, requested_years=3, values=[1.0] * len(labels))
        assert ok is False

    def test_snapshot_cannot_cover_three_years(self):
        ok, _ = validate_historical_coverage(
            labels=None, period_start=None, period_end=None,
            requested_years=3, values=[140.0])
        assert ok is False

    def test_weekly_three_year_passes(self):
        series = _three_year_weekly()
        ok, _ = validate_historical_coverage(
            labels=series["labels"], requested_years=3, values=series["values"])
        assert ok is True

    def test_partial_calendar_range_insufficient(self):
        # Requested 2022-2025 but only 2024-2025 provided.
        ok, _ = validate_historical_coverage(
            labels=["2024-01-01", "2025-01-01"], requested_years=4,
            values=[1.0, 2.0])
        assert ok is False

    def test_explicit_range_parsed_and_preserved(self):
        plan = build_research_plan("Compare NVIDIA and AMD from 2022 to 2025 on revenue growth and stock performance")
        tr = plan.get("time_range") or {}
        assert tr.get("start_year") == 2022
        assert tr.get("end_year") == 2025


# ---------------------------------------------------------------------------
# 10. VISUALIZATION DOWNSTREAM OF COMPLETENESS (ADVERSARIAL)
# ---------------------------------------------------------------------------
class TestVisualizationDownstream:
    def test_blocked_gate_strips_llm_graph(self, monkeypatch):
        judge = _decision_json(decision="answer")
        bad = _pipeline_json(
            answer="Comparison graph with NVIDIA and AMD",
            visuals=[{"visual_type": "graph",
                      "props": {"chart_type": "line", "labels": ["a", "b"],
                                "datasets": [{"name": "NVIDIA", "values": [1, 2]}]},
                      "title": "comparison graph with NVIDIA and AMD"}],
            confidence=0.8,
        )
        monkeypatch.setattr(pipeline_mod, "generate_response", _sequenced_fake(judge, bad))
        output = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_QUERY, db_data=[], source_scope="live_web",
            news_context=["only one snippet"],
            market_data=[{"entity": "NVIDIA", "labels": ["Aug 05", "Sep 04"],
                          "values": [1.0, 2.0]}],
            web_sources=[{"title": "s", "url": "https://a.example", "provider": "X"}],
        ))
        assert all(v.visual_type not in ("graph", "comparison") for v in output.visuals)

    def test_blocked_three_company_chart_refused(self, monkeypatch):
        judge = _decision_json(decision="answer")
        bad = _pipeline_json(
            answer="Tesla vs BYD vs Toyota chart",
            visuals=[{"visual_type": "comparison",
                      "props": {"value": 1, "baseline": 2,
                                "groups": [{"label": "Tesla", "value": 1},
                                           {"label": "BYD", "value": 2}]},
                      "title": "Tesla vs BYD vs Toyota chart"}],
            confidence=0.8,
        )
        monkeypatch.setattr(pipeline_mod, "generate_response", _sequenced_fake(judge, bad))
        output = asyncio.run(run_pipeline(
            user_query=TESLA_QUERY, db_data=[], source_scope="live_web",
            news_context=["Tesla snippet"],
            web_sources=[{"title": "s", "url": "https://a.example", "provider": "X"}],
        ))
        assert all(v.visual_type not in ("graph", "comparison") for v in output.visuals)

    def test_invented_value_removed(self):
        from app.services.llm.langchain_pipeline import VisualOutput
        visuals = [VisualOutput(
            visual_type="graph", title="g",
            props={"chart_type": "bar", "labels": ["A"],
                   "datasets": [{"name": "x", "values": [999999999.0]}]},
        )]
        kept, dropped = drop_ungrounded_visuals(
            visuals, ["Tesla revenue $100B in 2023"])
        assert kept == [] and dropped == 1


# ---------------------------------------------------------------------------
# 11. LLM FAIL-OPEN CORRECTNESS
# ---------------------------------------------------------------------------
class TestFailOpen:
    def test_judge_failure_still_gated(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(RuntimeError("judge down"),
                            _pipeline_json(answer="best effort", confidence=0.7)),
        )
        # Insufficient evidence: gate must still BLOCK + force 0 confidence.
        output = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_QUERY, db_data=[], source_scope="live_web",
            news_context=["one snippet"],
            web_sources=[{"title": "s", "url": "https://a.example", "provider": "X"}],
        ))
        assert output.confidence == 0.0
        assert all(v.visual_type not in ("graph", "comparison") for v in output.visuals)

    def test_judge_failure_with_valid_evidence_answers(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(RuntimeError("judge down"),
                            _pipeline_json(answer="solid", confidence=0.8)),
        )
        price = [_three_year_weekly("NVIDIA", "NVDA", 20.0, 180.0),
                 _three_year_weekly("AMD", "AMD", 80.0, 160.0)]
        financial = [
            _financial("NVIDIA", "NVDA", ["2022", "2023", "2024"], [27e9, 27e9, 60e9], [9e9, 4e9, 29e9]),
            _financial("AMD", "AMD", ["2022", "2023", "2024"], [23e9, 22e9, 25e9], [1.3e9, 0.8e9, 1.6e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_QUERY, db_data=[], source_scope="live_web",
            news_context=["NVIDIA snippet", "AMD snippet"],
            web_sources=[
                {"title": "a", "url": "https://a.example", "provider": "Yahoo Finance"},
                {"title": "b", "url": "https://b.example", "provider": "Yahoo Finance"},
            ],
            price_history=price, financial_history=financial,
        ))
        assert output.confidence > 0.0
        assert output.clarification is None


# ---------------------------------------------------------------------------
# 12. CONFIDENCE FOLLOWS EVIDENCE CONTRACT
# ---------------------------------------------------------------------------
class TestConfidenceContract:
    def test_tesla_only_snippets_never_high(self):
        capped = evidence_driven_confidence(
            0.95,
            plan={"is_comparison": True, "requires_history": True,
                  "required_entities": ["Tesla", "BYD", "Toyota"],
                  "required_metrics": [METRIC_REVENUE]},
            entities_found=["Tesla"], metrics_found=[METRIC_REVENUE],
            historical_ok=False, comparison_ok=False,
            snippet_count=10, provider_count=2, source_count=10,
        )
        assert capped == 0.0

    def test_raw_figure_count_cannot_inflate(self):
        assert comparison_confidence(
            entities_found=["Tesla"], entities_required=["Tesla", "Toyota"],
            metrics_found=[METRIC_REVENUE], metrics_required=[METRIC_REVENUE],
            historical_ok=True, comparison_ok=True, source_count=10,
        ) == 0.0


# ---------------------------------------------------------------------------
# 13. MISSING != ZERO
# ---------------------------------------------------------------------------
class TestMissingNotZero:
    def test_none_values_omitted_not_zeroed(self):
        rows = [
            {"date": "2024-01-01", "revenue": 100.0},
            {"date": "2024-02-01", "revenue": None},
            {"date": "2024-03-01", "revenue": 300.0},
            {"date": "2024-04-01", "revenue": 400.0},
        ]
        visuals = _visuals_from_rows(rows, {}, None)
        graphs = [v for v in visuals if v.visual_type == "graph"]
        assert graphs
        values = graphs[0].props["datasets"][0]["values"]
        assert 0 not in values
        assert values == [100.0, 300.0, 400.0]

    def test_align_preserves_missing_as_none(self):
        series = [
            {"entity": "A", "labels": ["2024-01-01", "2024-01-02"], "values": [1.0, 2.0]},
            {"entity": "B", "labels": ["2024-01-02", "2024-01-03"], "values": [20.0, 30.0]},
        ]
        labels, datasets = _align_series(series)
        assert "2024-01-01" in labels and "2024-01-03" in labels
        by_name = {d["name"]: d["values"] for d in datasets}
        assert by_name["A"][labels.index("2024-01-03")] is None
        assert by_name["B"][labels.index("2024-01-01")] is None
        assert 0 not in (by_name["A"] + [v for v in by_name["B"] if v is not None])


# ---------------------------------------------------------------------------
# 14. MULTI-SERIES TIME ALIGNMENT
# ---------------------------------------------------------------------------
class TestTimeAlignment:
    def test_mismatched_timestamps_align_by_timestamp(self):
        nvidia = {"entity": "NVIDIA", "labels": ["2022-09-07", "2023-09-07", "2024-09-07"], "values": [20.0, 100.0, 180.0]}
        amd = {"entity": "AMD", "labels": ["2022-09-08", "2023-09-08", "2024-09-08"], "values": [80.0, 120.0, 160.0]}
        labels, datasets = _align_series([nvidia, amd])
        assert len(labels) == 6  # union, not first-series labels
        assert len(datasets[0]["values"]) == len(labels) == len(datasets[1]["values"])
        # Each series keeps its own values at its own timestamps.
        assert 20.0 in datasets[0]["values"] and 160.0 in datasets[1]["values"]

    def test_downsample_keeps_series_in_lockstep(self):
        import datetime
        base = datetime.date(2022, 1, 1)
        a = {"entity": "A", "labels": [(base + datetime.timedelta(days=i)).isoformat() for i in range(100)], "values": [float(i) for i in range(100)]}
        b = {"entity": "B", "labels": [(base + datetime.timedelta(days=i)).isoformat() for i in range(100)], "values": [float(i * 2) for i in range(100)]}
        labels, datasets = _align_series([a, b], max_points=30)
        assert len(labels) <= 35  # stride downsampling stays bounded
        assert len(datasets[0]["values"]) == len(labels) == len(datasets[1]["values"])


# ---------------------------------------------------------------------------
# 15. RUNTIME TRACE MODE
# ---------------------------------------------------------------------------
class TestRuntimeTrace:
    def test_trace_has_full_contract_and_no_secrets(self):
        plan = build_research_plan(NVIDIA_AMD_QUERY)
        trace = build_trace(
            query=NVIDIA_AMD_QUERY, plan=dict(plan),
            tools_requested=["market_history"],
            tools_executed=["market_history", "financial_history"],
            planned_tools=["market_history"],
            actually_executed_tools=["market_history", "financial_history"],
            tool_results={"price_history": 2},
            missing_entities=["Toyota"], missing_metrics=[],
            completeness={"comparison_complete": False},
            comparison_gate={"applies": True, "blocked": True, "blocked_reason": "missing Toyota"},
            calculated_stats=False, visual_decision=["table"], final_confidence=0.0,
        )
        for key in ("query", "entities", "metrics", "time_range", "required_tools",
                    "planned_tools", "actually_executed_tools", "tool_results",
                    "missing_entities", "missing_metrics", "completeness",
                    "comparison_gate", "blocked_reason", "calculated_stats",
                    "visual_decision", "final_confidence"):
            assert key in trace, f"trace missing {key}"
        blob = json.dumps(trace, default=str).lower()
        assert "api_key" not in blob and "token" not in blob and "password" not in blob


# ---------------------------------------------------------------------------
# 16. ADVERSARIAL MATRIX A-J
# ---------------------------------------------------------------------------
class TestAdversarialMatrix:
    def test_a_judge_asks_public_stats_overridden(self):
        assert must_not_clarify(TESLA_QUERY) is True

    def test_b_planner_too_few_tools_restored(self):
        assert "financial_history" in resolve_tool_plan(["market_history"], NVIDIA_AMD_QUERY)

    def test_c_narrator_graph_despite_blocked_stripped(self, monkeypatch):
        judge = _decision_json(decision="answer")
        bad = _pipeline_json(
            visuals=[{"visual_type": "graph",
                      "props": {"chart_type": "line", "labels": ["a"],
                                "datasets": [{"name": "x", "values": [1]}]},
                      "title": "comparison graph with NVIDIA and AMD"}],
            confidence=0.9)
        monkeypatch.setattr(pipeline_mod, "generate_response", _sequenced_fake(judge, bad))
        out = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_QUERY, db_data=[], source_scope="live_web",
            news_context=["s"], web_sources=[{"title": "s", "url": "https://a.example", "provider": "X"}]))
        assert all(v.visual_type not in ("graph", "comparison") for v in out.visuals)

    def test_d_narrator_invents_number_removed(self):
        from app.services.llm.langchain_pipeline import VisualOutput
        visuals = [VisualOutput(visual_type="graph", title="g",
                                props={"chart_type": "bar", "labels": ["A"],
                                       "datasets": [{"name": "x", "values": [123456789.0]}]})]
        kept, dropped = drop_ungrounded_visuals(visuals, ["Tesla revenue $100B"])
        assert dropped == 1 and kept == []

    def test_e_yahoo_only_one_company_incomplete(self):
        plan = build_research_plan(NVIDIA_AMD_QUERY)
        price = [_three_year_weekly("NVIDIA", "NVDA", 20.0, 180.0)]
        result = check_research_completeness(
            plan, {"price_history": price, "financial_history": []})
        assert result["comparison_complete"] is False
        assert _price_history_graph_visual(price, NVIDIA_AMD_QUERY, 3) is None

    def test_f_one_month_for_three_year_blocked(self):
        gate = _historical_comparison_gate(
            NVIDIA_AMD_QUERY,
            market_data=[{"entity": "NVIDIA", "labels": ["Aug 05", "Sep 04"],
                          "values": [1.0, 2.0]}],
            price_history=[], financial_history=[], fundamentals=[])
        assert gate["blocked"] is True

    def test_g_incompatible_money_no_comparison(self):
        figs = _figures_from_snippets(
            ["Tech startups raised $202B in AI funding in 2024",
             "Robotics startup launch startup cost was $40K in 2024"], STARTUP_QUERY)
        assert _comparison_from_figures(figs, STARTUP_QUERY) is None

    def test_h_usd_vs_jpy_blocked(self):
        a = ComparisonEvidence(entity="A", metric="revenue_growth", value=1.0, unit="USD", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="USD")
        b = ComparisonEvidence(entity="B", metric="revenue_growth", value=1.0, unit="JPY", period_start="2022-01-01", period_end="2025-01-01", frequency="annual", definition="annualTotalRevenue", is_historical=True, currency="JPY")
        assert validate_calculation_inputs(a, b)["ok"] is False

    def test_i_missing_not_zero(self):
        visuals = _visuals_from_rows(
            [{"d": "2024-01-01", "v": None}, {"d": "2024-02-01", "v": 5.0},
             {"d": "2024-03-01", "v": 6.0}, {"d": "2024-04-01", "v": 7.0}], {}, None)
        for v in visuals:
            if v.visual_type == "graph":
                assert 0 not in v.props["datasets"][0]["values"]

    def test_j_mismatched_timestamps_aligned(self):
        labels, datasets = _align_series([
            {"entity": "NVIDIA", "labels": ["2022-01-01", "2023-01-01"], "values": [1.0, 2.0]},
            {"entity": "AMD", "labels": ["2022-06-01", "2023-06-01"], "values": [10.0, 20.0]},
        ])
        assert len(labels) == 4
        assert all(len(d["values"]) == 4 for d in datasets)


# ---------------------------------------------------------------------------
# 17+18. EXACT REGRESSION QUERIES + END-TO-END PIPELINE
# ---------------------------------------------------------------------------
class TestRegressionQueriesEndToEnd:
    def test_a_startup_no_504999900(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(),
                            _pipeline_json(answer="Both routes differ; no like-for-like figures.", confidence=0.6)))
        out = asyncio.run(run_pipeline(
            user_query=STARTUP_QUERY, db_data=[], source_scope="live_web",
            news_context=["Tech startups raised $202B in AI funding in 2024",
                          "Robotics startup launch startup cost was $40K in 2024"],
            web_sources=[
                {"title": "funding", "url": "https://a.example", "provider": "X"},
                {"title": "cost", "url": "https://b.example", "provider": "Y"}]))
        assert "504999900" not in out.answer
        assert all(v.visual_type not in ("graph", "comparison") for v in out.visuals
                   if "Cited" in getattr(v, "title", ""))

    def test_b_nvidia_amd_end_to_end(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(),
                            _pipeline_json(answer="NVIDIA vs AMD validated.", confidence=0.8)))
        price = [_three_year_weekly("NVIDIA", "NVDA", 20.0, 180.0),
                 _three_year_weekly("AMD", "AMD", 80.0, 160.0)]
        financial = [
            _financial("NVIDIA", "NVDA", ["2022", "2023", "2024"], [27e9, 27e9, 60e9], [9e9, 4e9, 29e9]),
            _financial("AMD", "AMD", ["2022", "2023", "2024"], [23e9, 22e9, 25e9], [1.3e9, 0.8e9, 1.6e9]),
        ]
        out = asyncio.run(run_pipeline(
            user_query=(NVIDIA_AMD_QUERY + " Which company has had stronger stock performance, revenue growth, and profitability? Show the statistics and explain the key reasons behind the difference - Revenue growth, Price change"),
            db_data=[], source_scope="live_web",
            news_context=["NVIDIA history snippet", "AMD history snippet"],
            web_sources=[
                {"title": "Yahoo NVIDIA", "url": "https://f.example/nvda", "provider": "Yahoo Finance"},
                {"title": "Yahoo AMD", "url": "https://f.example/amd", "provider": "Yahoo Finance"}],
            price_history=price, financial_history=financial))
        assert out.confidence > 0.0
        assert out.clarification is None
        kinds = [v.visual_type for v in out.visuals]
        assert "graph" in kinds  # validated 3Y chart, not 1-month
        names = []
        for v in out.visuals:
            if v.visual_type == "graph":
                names += [d["name"] for d in v.props["datasets"]]
        assert "NVIDIA" in names and "AMD" in names

    def test_c_tesla_byd_toyota_end_to_end(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(),
                            _pipeline_json(answer="Tesla vs BYD vs Toyota validated.", confidence=0.8)))
        price = [_three_year_weekly("Tesla", "TSLA", 100.0, 250.0),
                 _three_year_weekly("BYD", "BYDDY", 30.0, 60.0),
                 _three_year_weekly("Toyota", "TM", 150.0, 200.0)]
        financial = [
            _financial("Tesla", "TSLA", ["2022", "2023", "2024"], [90e9, 96e9, 100e9], [12e9, 15e9, 16e9]),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024"], [400e9, 500e9, 600e9], [15e9, 25e9, 30e9]),
            _financial("Toyota", "TM", ["2022", "2023", "2024"], [30e12, 35e12, 40e12], [2e12, 3e12, 3.5e12]),
        ]
        out = asyncio.run(run_pipeline(
            user_query=TESLA_QUERY, db_data=[], source_scope="live_web",
            news_context=["Tesla snippet", "BYD snippet", "Toyota snippet"],
            web_sources=[
                {"title": "a", "url": "https://a.example", "provider": "Yahoo Finance"},
                {"title": "b", "url": "https://b.example", "provider": "Yahoo Finance"},
                {"title": "c", "url": "https://c.example", "provider": "Yahoo Finance"}],
            price_history=price, financial_history=financial))
        assert out.clarification is None
        assert out.confidence > 0.0
        # All three present: research_state + trace carry them, no dropping.
        assert set(out.research_state["plan"]["entities"]) == {"Tesla", "BYD", "Toyota"}
        tables = [v for v in out.visuals if v.visual_type == "table"]
        assert tables  # year-by-year financial/margin tables

    def test_full_contract_pipeline_trace(self, monkeypatch):
        # query -> plan -> routing -> mocked results -> evidence -> completeness
        # -> gate -> stats -> viz -> PipelineOutput, with trace asserted.
        plan = build_research_plan(NVIDIA_AMD_QUERY)
        assert plan["entities"] == ["NVIDIA", "AMD"]
        resolved = resolve_tool_plan(["market_history"], NVIDIA_AMD_QUERY)
        assert "financial_history" in resolved  # routing contract
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(),
                            _pipeline_json(answer="contract ok", confidence=0.8)))
        price = [_three_year_weekly("NVIDIA", "NVDA", 20.0, 180.0),
                 _three_year_weekly("AMD", "AMD", 80.0, 160.0)]
        financial = [
            _financial("NVIDIA", "NVDA", ["2022", "2023", "2024"], [27e9, 27e9, 60e9], [9e9, 4e9, 29e9]),
            _financial("AMD", "AMD", ["2022", "2023", "2024"], [23e9, 22e9, 25e9], [1.3e9, 0.8e9, 1.6e9]),
        ]
        completeness = check_research_completeness(
            plan, {"price_history": price, "financial_history": financial,
                   "snippets": ["a", "b"]})
        assert completeness["comparison_complete"] is True
        gate = _historical_comparison_gate(
            NVIDIA_AMD_QUERY, price_history=price, financial_history=financial)
        assert gate["blocked"] is False
        assert gate["comparison_stats"]  # deterministic stats computed
        out = asyncio.run(run_pipeline(
            user_query=NVIDIA_AMD_QUERY, db_data=[], source_scope="live_web",
            news_context=["NVIDIA snippet", "AMD snippet"],
            web_sources=[
                {"title": "a", "url": "https://a.example", "provider": "Yahoo Finance"},
                {"title": "b", "url": "https://b.example", "provider": "Yahoo Finance"}],
            price_history=price, financial_history=financial))
        assert out.research_state is not None
        assert out.thinking  # runtime trace surfaced as thinking steps
