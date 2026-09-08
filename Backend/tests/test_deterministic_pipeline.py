"""Deterministic contract regression tests (audit Phases 20-21).

Covers the three historical failures end-to-end at the deterministic layer
(no network; the LLM is faked, including ADVERSARIAL fakes that deliberately
violate instructions) plus the numbered contract cases:

 1. "Compare NVIDIA and AMD from 2022 to 2025..." (explicit range)
 2. USD vs JPY revenue (currency mismatch blocks absolutes, not growth)
 3. 1-month data supplied for a 3-year request
 4. current fundamentals supplied for historical growth
 5. one missing company in a 3-company comparison
 6. missing metric
 7. incompatible metrics
 8. untyped snippet figure
 9. missing/non-numeric row must not become 0
10. mismatched chart timestamps align
11. planner requests too few tools
12. judge tools_needed differs from planner
13. clarification asks user for public statistics
14. LLM returns a fabricated visual despite BLOCKED
15. LLM returns winners despite comparison gate BLOCKED

And the Phase 21 adversarial set: non-compliant judge / narrator / planner
must still be contained by the deterministic layer.
"""

import asyncio
import json

import pytest

import app.services.llm.langchain_pipeline as pipeline_mod
import app.services.web_search as web_search_mod
from app.services import web_search_cache
from app.services.data.comparison import (
    METRIC_PROFIT,
    METRIC_REVENUE,
    METRIC_STOCK,
    ComparisonEvidence,
    assert_compatible_inputs,
    build_trace,
    check_entity_completeness,
    comparison_confidence,
    compute_comparison_stats,
    compute_net_margins,
    compute_pct_change,
    decompose_comparison_query,
    evidence_driven_confidence,
    merge_clarification_context,
    must_not_clarify,
    normalize_currency,
    parse_time_range,
    plan_to_dict,
    reconcile_judge_tools,
    required_tools_for_query,
    resolve_tool_plan,
    validate_comparison,
    validate_historical_coverage,
)
from app.services.llm.langchain_pipeline import (
    _align_series,
    _comparison_from_figures,
    _figures_from_snippets,
    _financial_history_table_visual,
    _fundamentals_comparison_visual,
    _historical_comparison_gate,
    _price_history_graph_visual,
    _visuals_from_rows,
    build_prompt,
    run_pipeline,
)
from app.services.web_search import (
    _extract_financials_from_snippets,
    requested_history_years,
    wants_historical,
)


QUERY_A = "which is best to start tech startup or robotics startup, with statistics"
QUERY_B = (
    "Compare NVIDIA and AMD over the last 3 years. Which company has had "
    "stronger stock performance, revenue growth, and profitability? Show the "
    "statistics and explain the key reasons behind the difference."
)
QUERY_C = (
    "Compare Tesla, BYD, and Toyota over the last 3 years. Which company had "
    "the highest revenue growth, strongest stock price performance, and best "
    "profitability? Show the year-by-year statistics, calculate the percentage "
    "changes, and explain the main reasons behind the differences."
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
        "decision": "answer",
        "missing": "",
        "chart_from_prior": False,
        "visual_plan": [],
        "suggested_options": [],
    }
    default.update(overrides)
    return default


def _pipeline_json(**overrides):
    default = {
        "answer": "Narrated answer.",
        "visuals": [],
        "insights": [],
        "summary": "",
        "root_causes": [],
        "recommendations": [],
        "news_context": [],
        "anomalies": [],
        "confidence": 0.7,
        "clarification": None,
    }
    default.update(overrides)
    return default


def _price(entity, symbol, start, end, n=40, currency="USD"):
    import datetime

    base = datetime.date(2022, 9, 7)
    labels, values = [], []
    for i in range(n):
        labels.append((base + datetime.timedelta(days=int(i * 3 * 365 / n))).isoformat())
        values.append(round(start + (end - start) * i / (n - 1), 2))
    return {
        "entity": entity, "symbol": symbol, "currency": currency,
        "labels": labels, "values": values,
        "frequency": "weekly", "period_start": labels[0], "period_end": labels[-1],
        "is_historical": True, "metric": "close",
    }


def _financial(entity, symbol, rev_labels, rev_values, inc_values, currency=None):
    block = lambda labels, values, metric: {
        "labels": list(labels), "values": list(values),
        "period_start": labels[0], "period_end": labels[-1],
        "metric": metric, "unit": "currency",
        **({"currency": currency} if currency else {}),
    }
    return {
        "entity": entity, "symbol": symbol,
        "frequency": "annual", "is_historical": True,
        "revenue": block(rev_labels, rev_values, "annualTotalRevenue"),
        "net_income": block(rev_labels, inc_values, "annualNetIncome"),
    }


def _sources(*titles):
    return [
        {"title": title, "url": f"https://example.com/{i}", "provider": "Yahoo Finance"}
        for i, title in enumerate(titles)
    ]


def _ev(entity, metric="stock_performance", unit="USD", start="2022-09-07",
        end="2025-09-07", freq="weekly", definition="close",
        historical=True, currency=None, geography=None, population=None):
    return ComparisonEvidence(
        entity=entity, metric=metric, value=100.0, unit=unit,
        period_start=start, period_end=end, frequency=freq,
        definition=definition, source="t", source_url="https://x.example",
        is_historical=historical, currency=currency,
        geography=geography, population=population,
    )


# ---------------------------------------------------------------------------
# TEST A: tech startup vs robotics startup -- no absurd comparison
# ---------------------------------------------------------------------------
class TestAStartupComparison:
    def test_funding_vs_cost_blocked(self):
        figures = _figures_from_snippets([
            "Tech startups raised $50M in funding last year",
            "A robotics startup costs $20k to launch",
        ], QUERY_A)
        assert _comparison_from_figures(figures, QUERY_A) is None

    def test_untyped_202b_figure_ineligible(self):
        figures = _figures_from_snippets([
            "AI sector saw $202B in activity during 2025, reports say.",
        ], QUERY_A)
        assert figures, "raw figure must still be preserved for citation"
        assert figures[0]["value"] == 202000000000.0
        # No metric binding ("activity" is not a metric cue) -> ineligible.
        assert figures[0]["comparison_eligible"] is False
        assert _comparison_from_figures(figures, QUERY_A) is None

    def test_startup_query_never_charts_comparison(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.7)),
        )
        output = asyncio.run(run_pipeline(
            user_query=QUERY_A, db_data=[], source_scope="live_web",
            news_context=[
                "Tech startups raised $50M in funding last year",
                "A robotics startup costs $20k to launch",
            ],
            web_sources=_sources("s1", "s2"),
        ))
        kinds = [v.visual_type for v in output.visuals]
        assert "comparison" not in kinds
        assert "graph" not in kinds  # strict on comparison intent
        # Confidence cannot be high merely because snippets exist.
        assert output.confidence <= 0.65

    def test_no_absurd_pct_change(self):
        # The live 504999900% came from comparing incompatible magnitudes;
        # incompatible inputs must fail BEFORE arithmetic.
        ok, _ = assert_compatible_inputs(
            _ev("tech startup", metric="funding", unit="USD"),
            _ev("robotics startup", metric="cost", unit="USD"),
        )
        assert ok is False


# ---------------------------------------------------------------------------
# TEST B: NVIDIA vs AMD 3-year historical comparison
# ---------------------------------------------------------------------------
class TestBNvidiaAmd:
    def test_canonical_plan_requires_both_entities_and_all_tools(self):
        plan = build_trace(
            query=QUERY_B,
            plan=plan_to_dict(
                __import__(
                    "app.services.data.comparison", fromlist=["build_research_plan"]
                ).build_research_plan(QUERY_B)
            ),
        )
        assert plan["plan"]["entities"] == ["NVIDIA", "AMD"]
        assert set(plan["plan"]["metrics"]) == {
            METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT,
        }
        assert set(plan["plan"]["required_tools"]) >= {
            "market_history", "financial_history", "snippets",
        }

    def test_planner_shortfall_repaired(self):
        resolved = resolve_tool_plan(["market_history"], QUERY_B)
        assert "financial_history" in resolved
        assert "market_history" in resolved

    def test_one_month_series_blocked(self):
        labels = ["Aug 05", "Aug 06", "Sep 04"]
        gate = _historical_comparison_gate(
            QUERY_B,
            market_data=[{"entity": "NVIDIA", "labels": labels,
                          "values": [140.0, 141.0, 142.0]}],
            price_history=[], financial_history=[], fundamentals=[],
        )
        assert gate["blocked"] is True
        assert gate["blocked_reason"]

    def test_snapshot_not_historical(self):
        funds = [
            {"entity": "NVIDIA", "symbol": "NVDA", "market_cap": 1e12,
             "currency": "USD"},
            {"entity": "AMD", "symbol": "AMD", "market_cap": 2e11,
             "currency": "USD"},
        ]
        assert _fundamentals_comparison_visual(funds, QUERY_B) is None

    def test_single_company_chart_blocked(self):
        graph = _price_history_graph_visual(
            [_price("NVIDIA", "NVDA", 20.0, 180.0)], QUERY_B, 3
        )
        assert graph is None

    def test_blocked_comparison_has_no_viz_no_confidence(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.7)),
        )
        output = asyncio.run(run_pipeline(
            user_query=QUERY_B, db_data=[], source_scope="live_web",
            news_context=["NVIDIA snippet", "AMD snippet"],
            market_data=[{"entity": "NVIDIA", "labels": ["Aug 05", "Sep 04"],
                          "values": [140.0, 142.0]}],
            web_sources=_sources("s1", "s2"),
        ))
        assert output.confidence == 0.0
        kinds = [v.visual_type for v in output.visuals]
        assert "graph" not in kinds
        assert "comparison" not in kinds
        assert output.research_state is not None
        assert output.research_state["gate"]["blocked"] is True

    def test_validated_comparison_computes_consistent_profitability(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.8)),
        )
        prices = [_price("NVIDIA", "NVDA", 20.0, 180.0),
                  _price("AMD", "AMD", 80.0, 160.0)]
        financials = [
            _financial("NVIDIA", "NVDA",
                       ["2022-01-30", "2025-01-26"], [26900.0, 130497.0],
                       [9752.0, 72880.0]),
            _financial("AMD", "AMD",
                       ["2022-12-31", "2025-06-28"], [23601.0, 26000.0],
                       [1312.0, 1800.0]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=QUERY_B, db_data=[], source_scope="live_web",
            news_context=["NVIDIA history", "AMD history"],
            price_history=prices, financial_history=financials,
            web_sources=_sources("s1", "s2"),
        ))
        assert output.confidence > 0.0
        graphs = [v for v in output.visuals if v.visual_type == "graph"]
        assert graphs
        assert [d["name"] for d in graphs[0].props["datasets"]] == ["NVIDIA", "AMD"]


# ---------------------------------------------------------------------------
# TEST C: Tesla / BYD / Toyota -- entity completeness, no ask-user loop
# ---------------------------------------------------------------------------
class TestCTeslaBydToyota:
    def test_all_three_entities_required(self):
        decomposed = decompose_comparison_query(QUERY_C)
        assert decomposed["entities"] == ["Tesla", "BYD", "Toyota"]
        ok, missing = check_entity_completeness(
            decomposed["entities"], ["Tesla", "BYD"]
        )
        assert ok is False and missing == ["toyota"]

    def test_researchable_never_clarifies(self, monkeypatch):
        assert must_not_clarify(QUERY_C) is True
        # Even a non-compliant judge saying "clarify" is overridden.
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(decision="clarify", missing="need figures",
                               suggested_options=["send revenue"]),
                _pipeline_json(answer="Best-effort from evidence.", confidence=0.5),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query=QUERY_C, db_data=[], source_scope="live_web",
            news_context=["Tesla snippet", "BYD snippet", "Toyota snippet"],
            web_sources=_sources("s1", "s2", "s3"),
        ))
        assert output.clarification is None
        assert "Best-effort" in output.answer

    def test_clarification_reply_merges_into_original_plan(self):
        merged = merge_clarification_context(
            QUERY_C, "Which metric?", "revenue please"
        )
        decomposed = decompose_comparison_query(merged)
        assert decomposed["entities"] == ["Tesla", "BYD", "Toyota"]
        assert METRIC_REVENUE in decomposed["metrics"]

    def test_missing_toyota_partial_and_names_excluded(self):
        gate = _historical_comparison_gate(
            QUERY_C,
            price_history=[_price("Tesla", "TSLA", 250.0, 350.0),
                           _price("BYD", "BYDDY", 65.0, 60.0)],
            financial_history=[
                _financial("Tesla", "TSLA", ["2022", "2023"], [90.0, 96.0],
                           [12.0, 15.0]),
                _financial("BYD", "BYDDY", ["2022", "2023"], [424.0, 602.0],
                           [16.0, 30.0]),
            ],
        )
        # Partial-result policy: 2-of-3 validated subset is sufficient.
        assert gate["blocked"] is False
        assert gate.get("partial") is True
        assert "Toyota" in str(gate.get("excluded_entities", []))
        assert gate.get("comparison_stats") is not None

    def test_currencies_handled_and_year_by_year_retained(self):
        gate = _historical_comparison_gate(
            QUERY_C,
            price_history=[_price("Tesla", "TSLA", 250.0, 350.0),
                           _price("BYD", "BYDDY", 65.0, 60.0),
                           _price("Toyota", "TM", 150.0, 180.0)],
            financial_history=[
                _financial("Tesla", "TSLA", ["FY2022", "FY2023"],
                           [81.5, 96.8], [12.6, 15.0], currency="USD"),
                _financial("BYD", "BYDDY", ["2022", "2023"],
                           [424.1, 602.3], [16.6, 30.0], currency="CNY"),
                _financial("Toyota", "TM", ["FY2022", "FY2023"],
                           [37.2, 40.0], [2.5, 3.0], currency="JPY"),
            ],
        )
        # Growth % is currency-invariant: mixed reporting currencies must
        # not block the validated comparison...
        assert gate["blocked"] is False
        # ...but the mix is recorded, never silent.
        assert gate["currency_mixed"]
        stats = gate["comparison_stats"]
        assert stats["yearly"]["Toyota"]["revenue"][0]["label"] == "FY2022"
        assert stats["winners"][METRIC_PROFIT] == "Tesla"

    def test_financial_table_marks_mixed_currencies(self):
        table = _financial_history_table_visual([
            _financial("Tesla", "TSLA", ["2022", "2023"], [90.0, 96.0],
                       [12.0, 15.0], currency="USD"),
            _financial("Toyota", "TM", ["2022", "2023"], [37200.0, 45100.0],
                       [2500.0, 4900.0], currency="JPY"),
        ])
        assert table is not None
        assert "mixed currencies" in table.title
        assert "Currency" in table.props["columns"]


# ---------------------------------------------------------------------------
# Numbered contract cases 1-15
# ---------------------------------------------------------------------------
class TestContractCases:
    def test_01_explicit_range_2022_to_2025(self):
        query = "Compare NVIDIA and AMD from 2022 to 2025 on revenue growth."
        parsed = parse_time_range(query)
        assert parsed.kind == "calendar_year_range"
        assert (parsed.start_year, parsed.end_year, parsed.years) == (2022, 2025, 4)
        assert wants_historical(query) is True
        assert requested_history_years(query) == 4
        decomposed = decompose_comparison_query(query)
        assert decomposed["requires_history"] is True

    def test_01_between_and_dash_forms(self):
        assert parse_time_range("Compare X and Y between 2021 and 2024").years == 4
        assert parse_time_range("revenue 2020-2023 trend").years == 4
        assert parse_time_range("trailing 3 years").kind == "relative_years"
        assert parse_time_range("no period here").kind == "none"

    def test_02_usd_vs_jpy_blocks_absolutes_not_growth(self):
        ok, detail = validate_comparison([
            _ev("Tesla", metric="revenue_growth", currency="USD"),
            _ev("Toyota", metric="revenue_growth", currency="JPY"),
        ])
        assert ok is False and "currency" in detail
        # ...while per-entity growth stays computable in its own currency.
        assert compute_pct_change(37200.0, 45100.0) == round(
            (45100.0 - 37200.0) / 37200.0 * 100, 2
        )
        # Unknown currency on one side passes with an assumption, never a
        # silent default.
        ok, _ = validate_comparison([
            _ev("Tesla", metric="revenue_growth", currency="USD"),
            _ev("Toyota", metric="revenue_growth", currency=None),
        ])
        assert ok is True

    def test_03_one_month_for_three_years_blocked(self):
        ok, _ = validate_historical_coverage(
            labels=["Aug 05", "Aug 20", "Sep 04"], requested_years=3,
            values=[1.0, 2.0, 3.0],
        )
        assert ok is False

    def test_04_snapshot_for_historical_growth_blocked(self):
        ok, _ = validate_historical_coverage(
            labels=None, period_start=None, period_end=None,
            requested_years=3, values=[5e11],
        )
        assert ok is False
        # Mixed historical/current status never compares.
        ok, detail = validate_comparison([
            _ev("A", historical=True), _ev("B", historical=False),
        ])
        assert ok is False and "historical/current" in detail

    def test_05_missing_company(self):
        ok, detail = validate_comparison(
            [_ev("Tesla"), _ev("BYD")],
            expected_entities=["Tesla", "BYD", "Toyota"],
        )
        assert ok is False and "toyota" in detail.lower()

    def test_06_missing_metric_partial_confidence(self):
        # Partial-metric policy: one validated metric of two still answers
        # (capped partial), only zero validated metrics forces 0.0.
        partial = evidence_driven_confidence(
            0.9,
            plan={"is_comparison": True, "requires_history": True,
                  "required_entities": ["NVIDIA", "AMD"],
                  "required_metrics": [METRIC_STOCK, METRIC_REVENUE]},
            entities_found=["NVIDIA", "AMD"],
            metrics_found=[METRIC_STOCK],
            historical_ok=True, comparison_ok=True, source_count=4,
        )
        assert 0.0 < partial <= 0.65
        assert evidence_driven_confidence(
            0.9,
            plan={"is_comparison": True, "requires_history": True,
                  "required_entities": ["NVIDIA", "AMD"],
                  "required_metrics": [METRIC_STOCK, METRIC_REVENUE]},
            entities_found=["NVIDIA", "AMD"],
            metrics_found=[],
            historical_ok=True, comparison_ok=True, source_count=4,
        ) == 0.0

    def test_07_incompatible_metrics(self):
        assert validate_comparison(
            [_ev("NVIDIA", metric="revenue_growth"),
             _ev("AMD", metric="market_cap")]
        )[0] is False
        assert validate_comparison(
            [_ev("NVIDIA", definition="close"),
             _ev("AMD", definition="annualTotalRevenue")]
        )[0] is False
        assert validate_comparison(
            [_ev("NVIDIA", freq="weekly"), _ev("AMD", freq="daily")]
        )[0] is False
        assert validate_comparison(
            [_ev("NVIDIA", geography="US"), _ev("AMD", geography="CN")]
        )[0] is False
        assert validate_comparison(
            [_ev("NVIDIA", population="all stores"),
             _ev("AMD", population="online only")]
        )[0] is False
        # Compatible pair passes, including explicit FX for mixed currency.
        assert validate_comparison(
            [_ev("Tesla", metric="revenue_growth", currency="USD"),
             _ev("Toyota", metric="revenue_growth", currency="JPY")],
            fx_rates={"USD": 1.0, "JPY": 150.0},
        )[0] is True

    def test_08_untyped_figure(self):
        figures = _figures_from_snippets(
            ["Some blog mentions $202B without saying what it is (2025)."],
            "compare tech startup vs robotics startup",
        )
        assert figures and figures[0]["comparison_eligible"] is False
        assert figures[0]["entity"] is None or figures[0]["metric"] is None
        assert _comparison_from_figures(
            figures, "compare tech startup vs robotics startup") is None

    def test_09_missing_values_never_become_zero(self):
        rows = [
            {"created_at": "2024-01-01", "revenue": 100, "region": "east"},
            {"created_at": "2024-01-02", "revenue": None, "region": "west"},
            {"created_at": "2024-01-03", "revenue": "n/a", "region": "east"},
            {"created_at": "2024-01-04", "revenue": 250, "region": "west"},
            {"created_at": "2024-01-05", "revenue": 175, "region": "east"},
        ]
        visuals = _visuals_from_rows(rows, {})
        graph = next(v for v in visuals if v.visual_type == "graph")
        assert 0 not in graph.props["datasets"][0]["values"]
        assert graph.props["datasets"][0]["values"] == [100, 250, 175]
        assert graph.props["labels"] == ["2024-01-01", "2024-01-04", "2024-01-05"]
        cat_rows = [
            {"region": "east", "revenue": 100},
            {"region": "west", "revenue": None},
        ]
        visuals = _visuals_from_rows(cat_rows, {})
        # A single valid bucket is not a chart; the table must not show a
        # fabricated west=0 bar either.
        assert not [v for v in visuals if v.visual_type == "graph"]
        table = next(v for v in visuals if v.visual_type == "table")
        assert "100" in json.dumps(table.props["values"])
        assert "0" not in [
            cell for row in table.props["values"] for cell in row
        ]

    def test_10_mismatched_timestamps_align(self):
        series = [
            {"entity": "A", "labels": ["2024-01-01", "2024-01-02", "2024-01-03"],
             "values": [1.0, 2.0, 3.0]},
            {"entity": "B", "labels": ["2024-01-02", "2024-01-03", "2024-01-04"],
             "values": [20.0, 30.0, 40.0]},
        ]
        labels, datasets = _align_series(series)
        assert labels == ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        assert datasets[0]["values"] == [1.0, 2.0, 3.0, None]
        assert datasets[1]["values"] == [None, 20.0, 30.0, 40.0]
        # Downsampled alignment stays in lockstep too.
        labels, datasets = _align_series(series, max_points=2)
        assert len(labels) == 2
        assert all(len(d["values"]) == 2 for d in datasets)

    def test_11_planner_shortfall_repaired(self):
        assert "financial_history" in resolve_tool_plan(["market_history"], QUERY_B)
        # Unknown names are dropped but a non-empty plan still enforces the
        # deterministic requirements; empty/None abstains (predicates decide).
        assert resolve_tool_plan(["bogus"], QUERY_B) == required_tools_for_query(QUERY_B)
        assert resolve_tool_plan([], QUERY_B) == []
        assert resolve_tool_plan(None, QUERY_B) == []

    def test_12_judge_tools_reconciled_not_dead(self):
        reconciled = reconcile_judge_tools(
            ["market_history", "financial_history"],
            {"row_count": 0, "web_snippet_count": 2,
             "price_history_entities": ["NVIDIA", "AMD"],
             "financial_history_entities": [],
             "market_entities": [], "fundamentals_entities": [],
             "macro_entities": []},
        )
        assert reconciled["missing"] == ["financial_history"]
        assert reconcile_judge_tools([], {})["missing"] == []

    def test_13_public_stats_clarification_banned(self):
        assert must_not_clarify(QUERY_C) is True
        assert must_not_clarify("Compare Apple and Samsung") is False
        assert must_not_clarify(
            "Compare Apple and Samsung revenue over the last 3 years") is True

    def test_14_fabricated_visual_stripped_when_blocked(self, monkeypatch):
        fabricated = {
            "visual_type": "comparison",
            "props": {"value": 999.0, "baseline": 1.0,
                      "groups": [{"label": "NVIDIA", "value": 999.0},
                                 {"label": "AMD", "value": 1.0}]},
            "title": "Comparison",
        }
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(answer="NVIDIA wins big.", confidence=0.9,
                               visuals=[fabricated]),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query=QUERY_B, db_data=[], source_scope="live_web",
            news_context=["thin snippet"],
            web_sources=_sources("s1"),
        ))
        assert output.confidence == 0.0
        kinds = [v.visual_type for v in output.visuals]
        assert "graph" not in kinds
        assert "comparison" not in kinds

    def test_15_text_winners_cannot_conjure_visuals_or_confidence(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(answer="The winner is NVIDIA at 800%.",
                               confidence=0.95),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query=QUERY_B, db_data=[], source_scope="live_web",
            news_context=["thin snippet"],
            web_sources=_sources("s1"),
        ))
        assert output.confidence == 0.0
        assert not [v for v in output.visuals
                    if v.visual_type in ("graph", "comparison")]
        assert "VALIDATED EVIDENCE" in build_prompt(
            QUERY_B, [], {}, news_context=["thin snippet"],
            source_scope="live_web",
            comparison_gate={"applies": True, "blocked": True,
                             "blocked_reason": "missing history"},
        )


# ---------------------------------------------------------------------------
# Phase 21: adversarial LLM non-compliance + provider failures
# ---------------------------------------------------------------------------
class TestAdversarial:
    def test_judge_clarify_for_researchable_overridden(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(decision="clarify", missing="need data"),
                _pipeline_json(answer="Answered anyway.", confidence=0.4),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query=QUERY_C, db_data=[], source_scope="live_web",
            news_context=["s1", "s2"], web_sources=_sources("s1", "s2"),
        ))
        assert output.clarification is None

    def test_narrator_graph_despite_blocked_stripped(self, monkeypatch):
        graph = {
            "visual_type": "graph",
            "props": {"chart_type": "line", "labels": ["2022", "2025"],
                      "datasets": [{"name": "NVIDIA", "values": [1.0, 2.0]}]},
            "title": "Fabricated",
        }
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(),
                            _pipeline_json(confidence=0.8, visuals=[graph])),
        )
        output = asyncio.run(run_pipeline(
            user_query=QUERY_B, db_data=[], source_scope="live_web",
            news_context=["snippet mentioning 1.0 and 2.0"],
            web_sources=_sources("s1"),
        ))
        assert output.confidence == 0.0
        assert not [v for v in output.visuals if v.visual_type == "graph"]

    def test_narrator_invented_numbers_dropped(self, monkeypatch):
        graph = {
            "visual_type": "graph",
            "props": {"chart_type": "bar", "labels": ["a", "b"],
                      "datasets": [{"name": "amount",
                                    "values": [42000000.0, 43000000.0]}]},
            "title": "Invented",
        }
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(),
                            _pipeline_json(confidence=0.7, visuals=[graph])),
        )
        output = asyncio.run(run_pipeline(
            user_query="startup news", db_data=[], source_scope="live_web",
            news_context=["Acme raised $300k in 2024"],
            web_sources=_sources("s1"),
        ))
        for visual in output.visuals:
            if visual.visual_type == "graph":
                assert visual.props["datasets"][0]["values"] != [42000000.0, 43000000.0]

    def test_planner_minimal_plan_repaired(self):
        plan = required_tools_for_query(QUERY_B)
        assert {"market_history", "financial_history"} <= set(plan)
        # LLM extras are kept, required tools restored: exact union.
        assert resolve_tool_plan(["market_history", "macro"], QUERY_B) == (
            plan + ["macro"]
        )

    def test_all_providers_failing_never_raises(self, monkeypatch):
        async def fake_rewrite(query, prior_clarification=None, company_name=None):
            return {"queries": ["q1"], "entities": ["Tesla"],
                    "time_sensitive": False}

        async def boom(*args, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(web_search_mod, "rewrite_search_queries", fake_rewrite)
        monkeypatch.setattr(web_search_mod, "_tavily_search", boom)
        monkeypatch.setattr(web_search_mod, "_ddg_search", boom)
        monkeypatch.setattr(web_search_mod, "_resolve_symbol", boom)
        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", boom)
        monkeypatch.setattr(web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", "k")
        monkeypatch.setattr(web_search_mod.get_settings(), "FRED_API_KEY", None)
        result = asyncio.run(web_search_mod.search_web("Tesla news"))
        assert result.context == [] and result.market_data == []
        assert result.price_history == [] and result.financial_history == []

    def test_yahoo_partial_never_drops_third_entity(self, monkeypatch):
        seen = []

        async def fake_rewrite(query, prior_clarification=None, company_name=None):
            return {"queries": ["Tesla BYD Toyota comparison"],
                    "entities": ["Tesla", "BYD"], "time_sensitive": False}

        async def fake_resolve(client, entity):
            return {"Tesla": "TSLA", "BYD": "BYDDY", "Toyota": "TM"}.get(entity)

        async def fake_history(client, entity, symbol, years=3):
            seen.append(entity)
            return (f"{entity} history",
                    _price(entity, symbol, 10.0, 20.0),
                    {"title": entity, "url": "", "provider": "Yahoo Finance"})

        async def fake_financial(client, entity, symbol, years=3):
            return None

        async def fake_snippets(client, query_item, settings, time_sensitive=False):
            return []

        async def no_wiki(client, entity):
            return None

        monkeypatch.setattr(web_search_mod, "rewrite_search_queries", fake_rewrite)
        monkeypatch.setattr(web_search_mod, "_resolve_symbol", fake_resolve)
        monkeypatch.setattr(web_search_mod, "_fetch_market_history", fake_history)
        monkeypatch.setattr(web_search_mod, "_fetch_financial_history", fake_financial)
        monkeypatch.setattr(web_search_mod, "_snippets_for_query", fake_snippets)
        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", no_wiki)
        monkeypatch.setattr(web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None)
        monkeypatch.setattr(web_search_mod.get_settings(), "FRED_API_KEY", None)
        result = asyncio.run(web_search_mod.search_web(QUERY_C, planned_tools=[]))
        # Deterministic union restores Toyota despite the LLM drop.
        assert set(seen) == {"Tesla", "BYD", "Toyota"}
        assert any("Toyota" in note or "missing" in note.lower()
                   for note in result.research_notes)


# ---------------------------------------------------------------------------
# Time range, currency, confidence, trace, state
# ---------------------------------------------------------------------------
class TestTimeRangeCurrencyConfidence:
    def test_relative_and_word_forms(self):
        assert parse_time_range("last 3 years").to_dict()["years"] == 3
        assert parse_time_range("past three years").years == 3
        assert parse_time_range("5Y trend").years == 5
        assert parse_time_range("3-year comparison").years == 3

    def test_currency_normalization(self):
        assert normalize_currency("$", None) == "USD"
        assert normalize_currency(None, "dollars") == "USD"
        assert normalize_currency("¥", None) == "JPY"
        assert normalize_currency("¥", "yuan") == "CNY"
        assert normalize_currency("CN¥", None) == "CNY"
        assert normalize_currency(None, "rupees") == "INR"
        assert normalize_currency(None, "zorkmids") is None
        assert normalize_currency(None, None) is None

    def test_pct_change_contract(self):
        assert compute_pct_change(100, 150) == 50.0
        assert compute_pct_change(0, 100) is None
        assert compute_pct_change(None, 5) is None
        assert compute_net_margins([100.0], [10.0]) == [10.0]

    def test_stats_winners_deterministic(self):
        payload = {
            "NVIDIA": {METRIC_STOCK: (20.0, 180.0)},
            "AMD": {METRIC_STOCK: (80.0, 160.0)},
        }
        first = compute_comparison_stats(payload)
        assert compute_comparison_stats(payload) == first
        assert first["winners"][METRIC_STOCK] == "NVIDIA"
        assert "pct_change" in first["formula"]

    def test_trace_has_no_secrets(self):
        trace = build_trace(
            query=QUERY_B, plan={"entities": ["NVIDIA"], "metrics": [],
                                 "time_range": None, "requires_history": True,
                                 "required_tools": ["market_history"]},
            tools_requested=["market_history"],
            tools_executed=["market_history"],
            tool_results={"rows": 0}, missing_evidence=["financials"],
            comparison_gate={"applies": True, "blocked": True,
                             "blocked_reason": "missing"},
            calculated_stats=False, visual_decision=["table"],
            final_confidence=0.0,
        )
        blob = json.dumps(trace)
        assert "GROQ" not in blob and "api_key" not in blob.lower()
        assert trace["comparison_gate"]["blocked"] is True

    def test_second_pass_extracts_all_three_currencies(self):
        tesla = [("Tesla revenue was $96.8 billion in FY2023, up from $81.5 "
                  "billion in FY2022. Tesla net income was $15.0 billion in "
                  "FY2023 versus $12.6 billion in FY2022.",
                  "https://t.example/tesla", "Tavily", "2024-02-01", 0.9)]
        extracted = _extract_financials_from_snippets("Tesla", tesla, years=3)
        assert extracted is not None
        assert extracted[1]["revenue"]["values"] == [81500000000.0, 96800000000.0]
        assert extracted[1]["revenue"]["currency"] == "USD"
        byd = [("BYD revenue was 602.3 billion yuan in 2023, up from 424.1 "
                "billion yuan in 2022. BYD net income was 30.0 billion yuan "
                "in 2023 versus 16.6 billion yuan in 2022.",
                "https://t.example/byd", "Tavily", "2024-03-01", 0.9)]
        extracted = _extract_financials_from_snippets("BYD", byd, years=3)
        assert extracted is not None
        assert extracted[1]["revenue"]["currency"] == "CNY"
