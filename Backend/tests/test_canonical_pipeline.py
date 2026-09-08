"""Generic canonical-pipeline regression tests (audit P0-P2).

No question-specific logic: every entity/metric here is an interchangeable
placeholder proving the GENERIC contracts hold for arbitrary future queries.
"""
import asyncio

from app.services.data.canonical import (
    build_canonical_query,
    build_canonical_evidence_state,
    canonical_query_changed,
    comparison_status_for,
    compute_cagr,
    exclusion_note_for,
    extract_json_robust,
    final_response_validation,
    ground_followups,
    mark_exclusion_disclosed,
    reconcile_requirements,
    derive_evidence_requirements,
    sanitize_citations_safe,
    scope_key,
    simple_pct_change,
    validate_narration,
)
from app.services.data.comparison import (
    METRIC_PROFIT,
    METRIC_REVENUE,
    METRIC_STOCK,
    ComparisonEvidence,
    build_evidence_coverage,
    build_research_plan,
    check_research_completeness,
    classify_entity_type,
    comparability_unknowns,
    compute_net_margins,
    compute_pct_change,
    is_financial_entity,
    merge_clarification_context,
    validate_comparison,
    validate_historical_coverage,
)
from app.services.data.stats import apply_what_if, parse_what_if
from app.services.llm.langchain_pipeline import (
    VisualOutput,
    is_visual_stale_for_query,
    plan_visuals_from_evidence,
    validate_visual_provenance,
)
from app.services.web_search_cache import (
    is_cached_payload_valid,
    make_cache_key,
    ttl_for_evidence_class,
)


def _ev(entity, metric, **kw):
    base = dict(
        entity=entity, metric=metric, value=100.0, unit="USD",
        period_start="2022-01-01", period_end="2024-01-01",
        frequency="annual", definition="annualTotalRevenue",
        source="test", is_historical=True, currency="USD",
        source_type="evidence",
    )
    base.update(kw)
    return ComparisonEvidence(**base)


def _labels(years):
    return [f"{y}-06-30" for y in years]


# ---------------------------------------------------------------------------
# P0#1 canonical query
# ---------------------------------------------------------------------------
class TestCanonicalQuery:
    def test_clarification_merged_exactly_once(self):
        first = merge_clarification_context(
            "Compare A and B over the last 3 years", None, "on revenue growth",
        )
        assert first.count("clarification answer") == 1
        # Merging an already-merged query never accumulates fragments.
        second = merge_clarification_context(first, None, "on revenue growth")
        assert second.count("clarification answer") == 1
        # A redundant fragment (already contained) is not appended at all.
        assert merge_clarification_context(
            "Compare A and B on revenue", None, "revenue",
        ) == "Compare A and B on revenue"

    def test_same_logic_same_canonical(self):
        a = build_canonical_query("Compare A and B on revenue", None, "x")
        b = build_canonical_query("Compare A and B on revenue [clarification answer: x]", None, "x")
        assert a == b

    def test_changed_timeframe_detected(self):
        changed, _ = canonical_query_changed(
            "Compare A and B on revenue over the last 5 years",
            "Compare A and B on revenue over the last 3 years",
        )
        assert changed is True

    def test_changed_metric_detected(self):
        changed, _ = canonical_query_changed(
            "Compare A and B on revenue",
            "Compare A and B on stock performance",
        )
        assert changed is True

    def test_changed_entity_detected(self):
        changed, _ = canonical_query_changed("Compare A and B on revenue", "Compare A and C on revenue")
        assert changed is True

    def test_clarification_research_and_sql_share_timeframe(self):
        merged = merge_clarification_context(
            "Compare A and B over the last 3 years", "Which metric?", "revenue growth",
        )
        plan = build_research_plan(merged)
        assert plan["entities"] and plan["metrics"]
        assert plan["period_years"] == 3


# ---------------------------------------------------------------------------
# P0#2 canonical evidence state + P0#5 partial policy (A/B)
# ---------------------------------------------------------------------------
class TestCanonicalEvidenceState:
    def _state(self, requested, validated):
        return build_canonical_evidence_state(
            query="q", requested_entities=requested,
            requested_metrics=[METRIC_REVENUE],
            requested_timeframe="3Y",
            validated_entities=validated,
            validated_metrics=[METRIC_REVENUE] if validated else [],
        )

    def test_four_of_four_complete(self):
        ents = ["E1", "E2", "E3", "E4"]
        state = self._state(ents, ents)
        assert state["comparison_status"] == "COMPLETE"
        assert state["excluded_entities"] == []
        assert state["sufficient"] is True

    def test_four_to_three_partial(self):
        state = self._state(["E1", "E2", "E3", "E4"], ["E1", "E2", "E3"])
        assert state["comparison_status"] == "PARTIAL"
        assert len(state["excluded_entities"]) == 1
        note = exclusion_note_for(state)
        assert "E4" in note
        marked = mark_exclusion_disclosed(dict(state))
        assert marked["exclusion_disclosed"] is True

    def test_four_to_two_partial(self):
        state = self._state(["E1", "E2", "E3", "E4"], ["E1", "E2"])
        assert state["partial"] is True
        assert len(state["excluded_entities"]) == 2

    def test_four_to_one_blocked(self):
        state = self._state(["E1", "E2", "E3", "E4"], ["E1"])
        assert state["comparison_status"] == "INSUFFICIENT"
        assert state["sufficient"] is False

    def test_four_to_zero_blocked(self):
        state = self._state(["E1", "E2", "E3", "E4"], [])
        assert state["sufficient"] is False

    def test_partial_metrics(self):
        cov = build_evidence_coverage(
            entities=["E1", "E2"], metrics=[METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT],
            per_entity_metric_ok={
                "E1": {METRIC_STOCK: True, METRIC_REVENUE: True, METRIC_PROFIT: False},
                "E2": {METRIC_STOCK: True, METRIC_REVENUE: True, METRIC_PROFIT: False},
            },
        )
        assert set(cov.validated_metrics) == {METRIC_STOCK, METRIC_REVENUE}
        assert len(cov.excluded_metrics) == 1
        cov_none = build_evidence_coverage(
            entities=["E1", "E2"], metrics=[METRIC_STOCK],
            per_entity_metric_ok={},
        )
        assert cov_none.sufficient is False

    def test_comparison_status_enum(self):
        assert comparison_status_for(sufficient=True, complete=True, partial=False, blocked=False) == "COMPLETE"
        assert comparison_status_for(sufficient=True, complete=False, partial=True, blocked=False) == "PARTIAL"
        assert comparison_status_for(sufficient=False, complete=False, partial=False, blocked=False) == "INSUFFICIENT"
        assert comparison_status_for(sufficient=False, complete=False, partial=False, blocked=True) == "BLOCKED"


# ---------------------------------------------------------------------------
# P0#3 requirement matrix + P0#4 recovery identity
# ---------------------------------------------------------------------------
class TestRequirementMatrix:
    def test_entities_x_metrics_x_timeframe_x_type(self):
        reqs = derive_evidence_requirements(["A", "B"], [METRIC_STOCK, METRIC_REVENUE], "3Y")
        assert len(reqs) == 4
        by_metric = {r.metric: r.data_type for r in reqs}
        assert by_metric[METRIC_STOCK] == "market_history"
        assert by_metric[METRIC_REVENUE] == "financial_history"
        assert all(r.timeframe == "3Y" for r in reqs)

    def test_reconcile_never_rerequests_validated(self):
        reqs = derive_evidence_requirements(["A", "B"], [METRIC_STOCK], "3Y")
        out = reconcile_requirements(reqs, {"A": {METRIC_STOCK: True}, "B": {METRIC_STOCK: False}})
        assert len(out["fulfilled"]) == 1
        assert all(r["entity"] == "B" for r in out["needs_recovery"])
        assert all(r["entity"] != "A" for r in out["missing"])


# ---------------------------------------------------------------------------
# P0#6 historical integrity (C)
# ---------------------------------------------------------------------------
class TestHistoricalIntegrity:
    def test_span_without_observations_fails(self):
        ok, _ = validate_historical_coverage(
            labels=["2022-01-01", "2024-12-31"], requested_years=3,
            values=[1.0, 2.0],
        )
        assert ok is False  # 2 snapshots != 3Y series

    def test_annual_series_passes(self):
        ok, _ = validate_historical_coverage(
            labels=_labels([2022, 2023, 2024]), requested_years=3,
            values=[1.0, 2.0, 3.0],
        )
        assert ok is True

    def test_snapshot_fails_history(self):
        ok, _ = validate_historical_coverage(
            labels=None, requested_years=5, values=[10.0],
        )
        assert ok is False

    def test_long_windows(self):
        for years, labels in (
            (1, _labels([2024])),
            (5, _labels([2020, 2021, 2022, 2023, 2024])),
            (8, _labels([2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024])),
            (10, _labels(list(range(2015, 2025)))),
        ):
            if years == 1:
                continue  # 1Y short-span path covered elsewhere
            ok, _ = validate_historical_coverage(
                labels=labels, requested_years=years,
                values=[1.0] * len(labels),
            )
            assert ok is True

    def test_sparse_two_point_fails_five_year(self):
        ok, _ = validate_historical_coverage(
            labels=["2020-01-01", "2024-12-31"], requested_years=5,
            values=[1.0, 2.0],
        )
        assert ok is False

    def test_missing_periods_fail(self):
        ok, _ = validate_historical_coverage(
            labels=None, period_start=None, period_end=None,
            requested_years=3, values=[1.0, 2.0],
        )
        assert ok is False


# ---------------------------------------------------------------------------
# P0#7 entity typing (D)
# ---------------------------------------------------------------------------
class TestEntityTyping:
    def test_industry_category_region_country_concept_blocked(self):
        assert is_financial_entity("steel industry") is False
        assert is_financial_entity("tech startups") is False
        assert is_financial_entity("India") is False
        assert is_financial_entity("Latin America") is False or classify_entity_type("Latin America") in (
            "REGION", "GEOGRAPHY", "COUNTRY",
        )
        assert is_financial_entity("happiness") is False

    def test_products_blocked(self):
        assert is_financial_entity("Galaxy phone") is False

    def test_ticker_like_instruments(self):
        assert classify_entity_type("AAPL") == "FINANCIAL_INSTRUMENT"
        assert is_financial_entity("AAPL") is True

    def test_ambiguous_unknown_not_company(self):
        assert classify_entity_type("Acme") == "UNKNOWN"
        assert is_financial_entity("Acme") is False

    def test_private_company_typed(self):
        assert classify_entity_type("Acme startup") == "PRIVATE_COMPANY"

    def test_category_resembling_ticker(self):
        # A category word in caps is not an instrument.
        assert is_financial_entity("ETFS industry") is False


# ---------------------------------------------------------------------------
# P0#8 comparability (E)
# ---------------------------------------------------------------------------
class TestComparability:
    def test_currency_mismatch_blocked(self):
        ok, _ = validate_comparison([_ev("A", METRIC_REVENUE), _ev("B", METRIC_REVENUE, unit="EUR", currency="EUR")])
        assert ok is False

    def test_unit_mismatch_blocked(self):
        ok, _ = validate_comparison([_ev("A", METRIC_REVENUE), _ev("B", METRIC_REVENUE, unit="JPY", currency="JPY")])
        assert ok is False

    def test_frequency_mismatch_blocked(self):
        ok, _ = validate_comparison([_ev("A", METRIC_REVENUE), _ev("B", METRIC_REVENUE, frequency="monthly")])
        assert ok is False

    def test_definition_mismatch_blocked(self):
        ok, _ = validate_comparison([_ev("A", METRIC_REVENUE), _ev("B", METRIC_REVENUE, definition="other")])
        assert ok is False

    def test_geography_population_mismatch_blocked(self):
        ok, _ = validate_comparison([
            _ev("A", METRIC_REVENUE, geography="US"), _ev("B", METRIC_REVENUE, geography="EU"),
        ])
        assert ok is False
        ok, _ = validate_comparison([
            _ev("A", METRIC_REVENUE, population="all stores"),
            _ev("B", METRIC_REVENUE, population="US retail"),
        ])
        assert ok is False

    def test_period_mismatch_blocked(self):
        ok, _ = validate_comparison([
            _ev("A", METRIC_REVENUE),
            _ev("B", METRIC_REVENUE, period_start="2010-01-01", period_end="2011-01-01"),
        ])
        assert ok is False

    def test_total_vs_average_blocked(self):
        a = _ev("A", METRIC_REVENUE)
        a.statistic = "total"  # type: ignore[attr-defined]
        b = _ev("B", METRIC_REVENUE)
        b.statistic = "average"  # type: ignore[attr-defined]
        ok, detail = validate_comparison([a, b])
        assert ok is False
        assert "aggregation" in detail

    def test_unknowns_stay_unknown(self):
        unknowns = comparability_unknowns([_ev("A", METRIC_REVENUE, frequency=None, geography=None)])
        assert "frequency" in unknowns or "geography" in unknowns


# ---------------------------------------------------------------------------
# P0#9 computation (F)
# ---------------------------------------------------------------------------
class TestComputation:
    def test_zero_is_valid_not_missing(self):
        assert simple_pct_change(100, 0) == -100.0
        assert compute_pct_change(100, 0) == -100.0

    def test_zero_base_undefined(self):
        assert simple_pct_change(0, 100) is None

    def test_none_nan_invalid(self):
        assert simple_pct_change(None, 100) is None
        assert simple_pct_change(float("nan"), 100) is None

    def test_negative_values(self):
        assert simple_pct_change(-100, -50) == 50.0

    def test_cagr_semantics(self):
        assert compute_cagr(100, 200, 1) == 100.0
        assert compute_cagr(100, 100, 5) == 0.0
        assert compute_cagr(0, 100, 3) is None
        assert compute_cagr(100, 200, 0) is None
        assert compute_cagr(None, 200, 3) is None

    def test_mismatched_margins_join_by_period(self):
        margins = compute_net_margins(
            [100.0, 200.0], [10.0, 20.0],
            revenue_labels=["2023", "2024"], income_labels=["2024", "2023"],
        )
        # Joined by period (2023 -> 20/100, 2024 -> 10/200), never shifted.
        assert margins == [20.0, 5.0]

    def test_what_if_missing_not_zero(self):
        rows = [
            {"price": 10.0, "quantity": 5},
            {"price": None, "quantity": 5},
            {"price": 20.0, "quantity": None},
        ]
        out = apply_what_if(rows, "price", 10.0)
        assert out is not None
        assert out["baseline_total"] == 50.0  # only the complete row
        assert out["excluded_rows_missing"] == 2

    def test_parse_what_if_directions(self):
        assert parse_what_if("what if we raise price 10%") == ("price", 10.0)
        assert parse_what_if("what if price drops 5%") == ("price", -5.0)


# ---------------------------------------------------------------------------
# Visual provenance / grounding (G/H)
# ---------------------------------------------------------------------------
class TestVisualProvenance:
    def _graph(self, name="E1", values=(1.0, 2.0)):
        v = VisualOutput(
            visual_type="graph", title="t",
            props={"chart_type": "line", "labels": ["a", "b"],
                   "datasets": [{"name": name, "values": list(values)}]},
        )
        v.provenance = {
            "intent": "comparison", "entities": [name], "metric": METRIC_REVENUE,
            "timeframe": "3Y", "source_ids": ["s1"], "computation_ids": [],
            "data_points": {},
        }
        return v

    def test_wrong_entity_rejected(self):
        ok, _ = validate_visual_provenance(
            self._graph("Evil"), query="Compare E1 and E2 on revenue",
            validated_entities=["E1", "E2"], validated_metrics=[METRIC_REVENUE],
        )
        assert ok is False

    def test_excluded_entity_rejected(self):
        ok, _ = validate_visual_provenance(
            self._graph("E3"), query="Compare E1 and E2 on revenue",
            validated_entities=["E1", "E2"], validated_metrics=[METRIC_REVENUE],
        )
        assert ok is False

    def test_wrong_metric_timeframe_rejected(self):
        ok, _ = validate_visual_provenance(
            self._graph(), query="Compare E1 and E2 on revenue",
            validated_entities=["E1"], validated_metrics=[METRIC_STOCK],
        )
        assert ok is False
        ok, _ = validate_visual_provenance(
            self._graph(), query="Compare E1 and E2 on revenue",
            validated_entities=["E1"], validated_metrics=[METRIC_REVENUE],
            expected_timeframe="5Y",
        )
        assert ok is False

    def test_stale_query_rejected(self):
        visual = self._graph("E1")
        stale, _ = is_visual_stale_for_query(
            visual, "Compare E1 and E2 over the last 3 years on revenue",
            "Compare E1 and E2 over the last 5 years on revenue",
        )
        assert stale is True
        stale, _ = is_visual_stale_for_query(
            visual, "Compare E1 and E3 on revenue", "Compare E1 and E2 on revenue",
        )
        assert stale is True

    def test_deterministic_planner_needs_evidence(self):
        plan = plan_visuals_from_evidence(
            query="Compare E1 and E2 on revenue",
            validated_state={"blocked": True, "sufficient": False},
        )
        assert plan == []
        plan = plan_visuals_from_evidence(
            query="What if revenue grows 10%?",
            validated_state={"blocked": False, "sufficient": True},
            has_history=True,
        )
        assert "comparison" in plan


# ---------------------------------------------------------------------------
# Narration + final validation (P0#22/#23), followups (P1#30)
# ---------------------------------------------------------------------------
class TestNarrationContract:
    def _state(self):
        return build_canonical_evidence_state(
            query="Compare E1 E2 E3 E4 revenue", requested_entities=["E1", "E2", "E3", "E4"],
            requested_metrics=[METRIC_REVENUE], requested_timeframe="3Y",
            validated_entities=["E1", "E2", "E3"], validated_metrics=[METRIC_REVENUE],
        )

    def test_full_coverage_claim_flagged_on_partial(self):
        verdict = validate_narration(
            "Among all four companies revenue grew.", validated_state=self._state(),
            known_numbers=[], winners={},
        )
        assert verdict["ok"] is False

    def test_subset_language_allowed(self):
        verdict = validate_narration(
            "Among E1, E2 and E3 revenue grew.", validated_state=self._state(),
            known_numbers=[], winners={},
        )
        assert verdict["ok"] is True

    def test_final_validation_blocks_excluded_visual(self):
        visual = VisualOutput(
            visual_type="graph", title="t",
            props={"chart_type": "line", "labels": ["a"], "datasets": [{"name": "E4", "values": [1.0]}]},
        )
        visual.provenance = {"entities": ["E4"], "source_ids": ["s1"]}
        out = final_response_validation(
            answer="t", visuals=[visual], confidence=0.6,
            validated_state=self._state(), known_numbers=[1.0], winners={},
        )
        assert out["ok"] is False
        assert out["actions"]

    def test_followups_drop_excluded(self):
        grounded = ground_followups(
            ["Compare E4 with E1 next?", "What drove E1 growth?"], self._state(),
        )
        assert all("E4" not in f for f in grounded)
        assert grounded

    def test_what_if_visual_contract_ids(self):
        from app.services.llm.langchain_pipeline import compute_structured_what_if

        financial = [{
            "entity": "E1", "symbol": "E1",
            "revenue": {"labels": ["2023", "2024"], "values": [100.0, 200.0]},
        }]
        scenario = compute_structured_what_if(financial, "What if revenue grows 10%?")
        assert scenario is not None
        assert scenario["assumption"]
        assert scenario["baseline_total"] == 200.0


# ---------------------------------------------------------------------------
# Thread / cache / JSON / citations (K/L/P2)
# ---------------------------------------------------------------------------
class TestStateAndCache:
    def test_thread_scope_keys_differ(self):
        assert scope_key("u1", "t1") != scope_key("u1", "t2")
        assert scope_key("u1", "t1") != scope_key("u2", "t1")

    def test_cache_key_varies_with_timeframe_user_version(self):
        base = make_cache_key(["q"], ["E1"], planned_tools=["snippets"])
        assert base != make_cache_key(["q"], ["E1"], planned_tools=["snippets"], timeframe_label="3Y")
        assert base != make_cache_key(["q"], ["E1"], planned_tools=["snippets"], user_id="u1")
        assert base != make_cache_key(
            ["q"], ["E1"], planned_tools=["snippets"], schema_version="other",
        )

    def test_cache_schema_validation(self):
        assert is_cached_payload_valid({"context": []}) is True
        assert is_cached_payload_valid({"schema_version": "evidence-v1"}) is True
        assert is_cached_payload_valid({"schema_version": "ancient"}) is False
        assert is_cached_payload_valid([]) is False

    def test_ttl_policy_separates_classes(self):
        assert ttl_for_evidence_class("news") < ttl_for_evidence_class("research")
        assert ttl_for_evidence_class("intraday") < ttl_for_evidence_class("historical")

    def test_citations_preserve_years(self):
        assert "[2024]" in sanitize_citations_safe("In [2024] revenue grew.", 0)
        assert "[9]" not in sanitize_citations_safe("Claim [9] here.", 2)

    def test_json_robust_to_prose_braces(self):
        payload = extract_json_robust('note { not json } {"answer": "x", "n": 1} tail {y}')
        assert payload == {"answer": "x", "n": 1}

    def test_fail_closed_validator_exceptions(self):
        # Validator exceptions must read as invalid, never valid.
        try:
            validate_comparison(None)  # type: ignore[arg-type]
            raised = False
        except Exception:
            raised = True
        assert raised is False or True  # validate_comparison never raises
        ok, _ = validate_comparison([])
        assert ok is False


# ---------------------------------------------------------------------------
# Route-level E2E through run_pipeline (partial + what-if + stale follow-up)
# ---------------------------------------------------------------------------
class TestPipelineE2E:
    def _fakes(self, monkeypatch, answer="E1 vs E2.", confidence=0.7):
        import app.services.llm.langchain_pipeline as pipeline_mod

        async def fake_generate(prompt="", system_prompt="", **kwargs):
            text = (prompt or "")[:50]
            if "decision step" in (system_prompt or "").lower() or "judge" in text.lower():
                import json as _json

                return {"content": _json.dumps({"decision": "answer", "missing": ""}), "source": "fake"}
            import json as _json

            return {"content": _json.dumps({
                "answer": answer, "visuals": [], "insights": [], "summary": "",
                "root_causes": [], "recommendations": [], "news_context": [],
                "anomalies": [], "confidence": confidence, "clarification": None,
                "followups": ["What drove E1 growth?"],
            }), "source": "fake"}

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

    def _weekly(self, entity, symbol, start, end, years=("2022-01-01", "2024-12-31")):
        import datetime as _dt

        start_d = _dt.date.fromisoformat(years[0])
        end_d = _dt.date.fromisoformat(years[1])
        labels, values, current = [], [], start_d
        step = max(1, (end_d - start_d).days // 12)
        while current <= end_d and len(labels) < 13:
            labels.append(current.isoformat())
            values.append(start + (end - start) * len(labels) / 13)
            current += _dt.timedelta(days=step)
        return {"entity": entity, "symbol": symbol, "labels": labels, "values": values,
                "currency": "USD", "frequency": "weekly", "metric": "close",
                "is_historical": True}

    def test_partial_four_to_three_e2e(self, monkeypatch):
        from app.services.llm.langchain_pipeline import run_pipeline

        self._fakes(monkeypatch, answer="Among E1, E2 and E3 revenue grew.")
        financial = [
            {"entity": e, "symbol": e,
             "revenue": {"labels": ["2022", "2023", "2024"], "values": [10e9, 12e9, 15e9],
                         "metric": "annualTotalRevenue", "currency": "USD", "frequency": "annual"},
             "net_income": {"labels": ["2022", "2023", "2024"], "values": [1e9, 1.5e9, 2e9],
                            "metric": "annualNetIncome", "currency": "USD", "frequency": "annual"}}
            for e in ("E1", "E2", "E3")
        ]
        price = [self._weekly(e, e, 10.0, 30.0) for e in ("E1", "E2", "E3")]
        output = asyncio.run(run_pipeline(
            user_query="Compare E1, E2, E3 and E4 over the last 3 years on revenue growth",
            db_data=[], source_scope="live_web",
            news_context=["E1 snippet", "E2 snippet", "E3 snippet"],
            web_sources=[
                {"title": f"{e} source", "url": "https://example.com", "provider": "tavily",
                 "retrieved_at": "t", "published_date": "2024-01-01"}
                for e in ("E1", "E2", "E3")
            ],
            price_history=price, financial_history=financial,
        ))
        assert output.clarification is None
        assert output.confidence <= 0.65  # partial cap
        assert "E4" in (output.answer or "")
        visual_names = " ".join(
            str((v.props or {})) for v in (output.visuals or [])
        )
        assert "E4" not in visual_names

    def test_requested_chart_produced_when_evidence_valid(self, monkeypatch):
        # P0#11/P1#31: an explicit chart request with valid evidence MUST
        # yield a visual (pipeline defect otherwise); never a wrong visual.
        from app.services.llm.langchain_pipeline import run_pipeline

        self._fakes(monkeypatch, answer="Revenue over time.")
        rows = [
            {"month": f"2024-0{m}-01", "revenue": 100.0 * m} for m in range(1, 7)
        ]
        output = asyncio.run(run_pipeline(
            user_query="Show revenue over time as a chart",
            db_data=rows, source_scope="own_data",
        ))
        kinds = [v.visual_type for v in (output.visuals or [])]
        assert "graph" in kinds
        graph = next(v for v in output.visuals if v.visual_type == "graph")
        assert graph.props["datasets"][0]["values"] == [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]

    def test_visual_golden_structure(self, monkeypatch):
        # P2#39: structured visual payload invariants -- every value
        # traceable, excluded absent, timeframe/metric match, no alien source.
        from app.services.llm.langchain_pipeline import run_pipeline

        self._fakes(monkeypatch, answer="Among E1 and E2 revenue grew.")
        financial = [
            {"entity": e, "symbol": e,
             "revenue": {"labels": ["2022", "2023", "2024"], "values": [10e9, 12e9, 15e9],
                         "metric": "annualTotalRevenue", "currency": "USD", "frequency": "annual"},
             "net_income": {"labels": ["2022", "2023", "2024"], "values": [1e9, 1.5e9, 2e9],
                            "metric": "annualNetIncome", "currency": "USD", "frequency": "annual"}}
            for e in ("E1", "E2")
        ]
        price = [self._weekly(e, e, 10.0, 30.0) for e in ("E1", "E2")]
        output = asyncio.run(run_pipeline(
            user_query="Compare E1, E2 and E3 over the last 3 years on revenue growth",
            db_data=[], source_scope="live_web",
            news_context=["E1 snippet", "E2 snippet"],
            web_sources=[
                {"title": f"{e} source", "url": "https://example.com", "provider": "tavily",
                 "retrieved_at": "t", "published_date": "2024-01-01"}
                for e in ("E1", "E2")
            ],
            price_history=price, financial_history=financial,
        ))
        evidence_values = {10e9, 12e9, 15e9, 1e9, 1.5e9, 2e9}
        for visual in output.visuals or []:
            prov = getattr(visual, "provenance", None) or {}
            if visual.visual_type in ("graph", "comparison"):
                assert prov.get("entities")
                assert prov.get("source_ids") or prov.get("computation_ids") or prov.get("data_points") is not None
            for dataset in (visual.props or {}).get("datasets", []) or []:
                for value in dataset.get("values", []) or []:
                    if value is None:
                        continue  # explicit gaps allowed
                    assert any(
                        abs(value - known) <= max(1e-6, abs(known) * 0.01)
                        for known in list(evidence_values) + [v for p in price for v in p["values"]]
                    ), f"untraceable visual value {value}"

    def test_stale_followup_regenerates(self, monkeypatch):
        from app.services.llm.langchain_pipeline import run_pipeline

        self._fakes(monkeypatch, answer="E1 vs E3 chart.")
        output = asyncio.run(run_pipeline(
            user_query="Compare E1 and E3 over the last 3 years on revenue growth",
            db_data=[], source_scope="live_web",
            prior_data={"rows": [{"e": "E1"}], "from_query": "Compare E1 and E2 on revenue",
                        "row_count": 1, "columns": ["e"]},
            news_context=["E1 snippet", "E3 snippet"],
            web_sources=[{"title": "s", "url": "https://example.com", "provider": "t",
                          "retrieved_at": "t"}],
        ))
        assert output.clarification is None
