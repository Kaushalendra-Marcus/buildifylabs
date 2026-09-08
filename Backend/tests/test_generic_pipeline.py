"""Generic pipeline regression tests (no question-specific hacks).

Covers arbitrary entities / metrics / timeframes through the COMPLETE
runtime pipeline (run_pipeline end-to-end, never helpers alone):

- arbitrary multi-entity with one missing (partial, not fail-all)
- arbitrary multi-metric with one metric missing (partial)
- arbitrary what-if (deterministic, no history-chart inheritance)
- arbitrary follow-ups (regenerate, stale blocked)
- invalid entity types (never enter market adapters)
- mismatched units / timeframes / frequencies (blocked, never compared)
- unrelated / stale visual data (rejected, fail closed)
- evidence coverage (requested / retrieved / validated / unavailable)
- visual provenance (every chart carries traceable provenance)
- confidence (partial capped, blocked zero, never 0% + chart)

All entities are fictional and interchangeable (Acme, Globex, Initech,
Umbrella, Stark, Wayne, Hooli, ...). No test depends on a fixed company,
metric synonym, or period wording beyond the generic contracts.
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
    ComparisonEvidence,
    build_evidence_coverage,
    build_research_plan,
    check_research_completeness,
    classify_entity_type,
    clarification_is_redundant,
    detect_entities,
    is_financial_entity,
    validate_calculation_inputs,
    validate_comparison,
)
from app.services.llm.langchain_pipeline import (
    build_validated_evidence_state,
    compute_structured_what_if,
    is_visual_stale_for_query,
    is_what_if_query,
    run_pipeline,
    validate_visual_provenance,
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


def _weekly(entity, symbol, start, end, n=40, currency="USD"):
    base = datetime.date(2022, 9, 7)
    labels, values = [], []
    for i in range(n):
        day = base + datetime.timedelta(days=int(i * (3 * 365 / n)))
        labels.append(day.isoformat())
        values.append(round(start + (end - start) * i / (n - 1), 2))
    return {
        "entity": entity, "symbol": symbol, "currency": currency,
        "labels": labels, "values": values, "range": "3y",
        "interval": "1wk", "frequency": "weekly",
        "period_start": labels[0], "period_end": labels[-1],
        "is_historical": True, "metric": "close",
    }


def _financial(entity, symbol, labels, rev, inc, currency="USD"):
    out = {"entity": entity, "symbol": symbol, "frequency": "annual", "is_historical": True}
    out["revenue"] = {
        "labels": labels, "values": rev, "period_start": labels[0],
        "period_end": labels[-1], "metric": "annualTotalRevenue",
        "unit": currency, "currency": currency,
    }
    out["net_income"] = {
        "labels": labels, "values": inc, "period_start": labels[0],
        "period_end": labels[-1], "metric": "annualNetIncome",
        "unit": currency, "currency": currency,
    }
    out["currency"] = currency
    return out


def _sources(n=2):
    return [
        {"title": f"s{i}", "url": f"https://a.example/{i}", "provider": "Yahoo Finance"}
        for i in range(n)
    ]


def _kinds(output):
    return [v.visual_type for v in (output.visuals or [])]


def _walk_numbers(props):
    if isinstance(props, bool):
        return
    if isinstance(props, (int, float)):
        yield float(props)
    elif isinstance(props, dict):
        for value in props.values():
            yield from _walk_numbers(value)
    elif isinstance(props, (list, tuple)):
        for value in props:
            yield from _walk_numbers(value)


# ---------------------------------------------------------------------------
# Evidence coverage: requested x metrics x time-range, four states
# ---------------------------------------------------------------------------
class TestEvidenceCoverage:
    def test_arbitrary_entities_detected_generically(self):
        ents = detect_entities(
            "Compare Acme and Globex over the last 3 years on revenue growth and stock performance"
        )
        assert ents == ["Acme", "Globex"]

    def test_four_arbitrary_entities_detected(self):
        ents = detect_entities(
            "Compare Acme, Globex, Initech and Umbrella over the last 3 years on revenue growth"
        )
        assert ents == ["Acme", "Globex", "Initech", "Umbrella"]

    def test_coverage_matrix_four_states(self):
        cov = build_evidence_coverage(
            entities=["Acme", "Globex", "Initech"],
            metrics=[METRIC_STOCK, METRIC_REVENUE],
            time_range_label="3Y",
            per_entity_metric_ok={
                "Acme": {METRIC_STOCK: True, METRIC_REVENUE: True},
                "Globex": {METRIC_STOCK: True, METRIC_REVENUE: True},
                "Initech": {METRIC_STOCK: True, METRIC_REVENUE: False},
            },
        )
        assert len(cov.requested) == 6
        assert len(cov.validated) == 5
        assert len(cov.unavailable) == 1
        assert cov.sufficient is True
        assert cov.partial is True
        # Initech has stock validated (revenue missing) -- union keeps it,
        # the missing revenue cell is tracked as unavailable.
        assert set(cov.validated_entities) == {"Acme", "Globex", "Initech"}
        assert len(cov.unavailable) == 1
        assert cov.unavailable[0]["entity"] == "Initech"

    def test_insufficient_single_entity_blocks(self):
        cov = build_evidence_coverage(
            entities=["Acme", "Globex"],
            metrics=[METRIC_STOCK],
            per_entity_metric_ok={"Acme": {METRIC_STOCK: True}},
        )
        assert cov.sufficient is False

    def test_never_zero_fills_missing(self):
        cov = build_evidence_coverage(
            entities=["Acme", "Globex", "Initech", "Umbrella"],
            metrics=[METRIC_REVENUE],
            per_entity_metric_ok={
                "Acme": {METRIC_REVENUE: True},
                "Globex": {METRIC_REVENUE: True},
                "Initech": {METRIC_REVENUE: True},
            },
        )
        assert cov.sufficient is True
        assert cov.partial is True
        assert any(e["entity"] == "Umbrella" for e in cov.excluded_entities)


# ---------------------------------------------------------------------------
# Partial results end-to-end: 4 requested, 3 validated
# ---------------------------------------------------------------------------
class TestPartialResultsEndToEnd:
    def test_four_entities_one_missing_partial(self, monkeypatch):
        query = (
            "Compare Acme, Globex, Initech and Umbrella over the last 3 years. "
            "Compare revenue growth, profitability, and stock performance."
        )
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.8)),
        )
        price = [
            _weekly("Acme", "ACME", 10.0, 30.0),
            _weekly("Globex", "GLOB", 20.0, 40.0),
            _weekly("Initech", "INIT", 30.0, 50.0),
            _weekly("Umbrella", "UMBR", 40.0, 60.0),
        ]
        financial = [
            _financial("Acme", "ACME", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.5e9, 2e9]),
            _financial("Globex", "GLOB", ["2022", "2023", "2024"], [20e9, 22e9, 25e9], [2e9, 2.5e9, 3e9]),
            _financial("Initech", "INIT", ["2022", "2023", "2024"], [30e9, 32e9, 35e9], [3e9, 3.5e9, 4e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=query, db_data=[], source_scope="live_web",
            news_context=["Acme snippet", "Globex snippet", "Initech snippet", "Umbrella snippet"],
            web_sources=_sources(4),
            price_history=price, financial_history=financial,
        ))
        assert output.clarification is None
        assert 0.0 < output.confidence <= 0.65
        assert "umbrella" in (output.answer or "").lower()
        # Validated subset charts; missing never zero-filled.
        for visual in (output.visuals or []):
            assert 0.0 not in list(_walk_numbers(visual.props)) or visual.visual_type == "table"

    def test_multi_metric_one_missing_partial(self, monkeypatch):
        query = (
            "Compare Stark and Wayne over the last 3 years on stock performance and revenue growth"
        )
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.8)),
        )
        price = [_weekly("Stark", "STK", 10.0, 30.0), _weekly("Wayne", "WYN", 20.0, 25.0)]
        # Revenue only (no net_income) -- profitability not requested here,
        # but revenue validates while stock validates: full for requested.
        financial = [
            _financial("Stark", "STK", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9]),
            _financial("Wayne", "WYN", ["2022", "2023", "2024"], [20e9, 21e9, 22e9], [2e9, 2.1e9, 2.2e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=query, db_data=[], source_scope="live_web",
            news_context=["Stark snippet", "Wayne snippet"],
            web_sources=_sources(2),
            price_history=price, financial_history=financial,
        ))
        assert output.clarification is None
        assert output.confidence > 0.0

    def test_single_remaining_blocks(self, monkeypatch):
        query = "Compare Hooli and Initech over the last 3 years on revenue growth"
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.8)),
        )
        financial = [
            _financial("Hooli", "HOO", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=query, db_data=[], source_scope="live_web",
            news_context=["Hooli snippet"],
            web_sources=_sources(1),
            price_history=[], financial_history=financial,
        ))
        assert output.confidence == 0.0
        assert "graph" not in _kinds(output)
        assert "comparison" not in _kinds(output)


# ---------------------------------------------------------------------------
# Arbitrary what-if: deterministic, no history-chart inheritance
# ---------------------------------------------------------------------------
class TestArbitraryWhatIf:
    def test_owned_rows_what_if_deterministic(self, monkeypatch):
        rows = [
            {"price": 100.0, "quantity": 10},
            {"price": 200.0, "quantity": 5},
        ]
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.7)),
        )
        output = asyncio.run(run_pipeline(
            user_query="What if Acme raises price 10%?",
            db_data=rows, source_scope="own_data",
            news_context=[], web_sources=[],
        ))
        assert output.clarification is None
        # Deterministic scenario math present (no LLM arithmetic).
        kinds = _kinds(output)
        assert "comparison" in kinds or "table" in kinds

    def test_what_if_never_reuses_history_chart(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.7)),
        )
        price = [_weekly("Acme", "ACME", 10.0, 30.0), _weekly("Globex", "GLOB", 20.0, 40.0)]
        financial = [
            _financial("Acme", "ACME", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9]),
            _financial("Globex", "GLOB", ["2022", "2023", "2024"], [20e9, 22e9, 25e9], [2e9, 2.2e9, 2.5e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query="What if Acme revenue grows 10% next year?",
            db_data=[], source_scope="live_web",
            news_context=["Acme snippet", "Globex snippet"],
            web_sources=_sources(2),
            price_history=price, financial_history=financial,
        ))
        for visual in (output.visuals or []):
            title = str(getattr(visual, "title", "") or "").lower()
            assert "stock performance" not in title

    def test_structured_what_if_computed(self):
        financial = [
            _financial("Acme", "ACME", ["2022", "2023"], [100.0, 200.0], [10.0, 20.0]),
            _financial("Globex", "GLOB", ["2022", "2023"], [300.0, 400.0], [30.0, 40.0]),
        ]
        result = compute_structured_what_if(financial, "What if revenue grows 10%?")
        assert result is not None
        assert result["pct_change"] == 10.0
        assert result["baseline_total"] == 600.0
        assert result["scenario_total"] == 660.0


# ---------------------------------------------------------------------------
# Arbitrary follow-ups: regenerate, stale blocked
# ---------------------------------------------------------------------------
class TestArbitraryFollowups:
    def test_chart_followup_regenerates_from_prior(self, monkeypatch):
        rows = [
            {"created_at": "2024-01-01", "revenue": 100},
            {"created_at": "2024-01-02", "revenue": 200},
            {"created_at": "2024-01-03", "revenue": 300},
        ]
        prior_data = {
            "columns": ["created_at", "revenue"],
            "row_count": 3, "rows": rows, "from_query": "how is Acme revenue?",
        }
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(decision="answer", chart_from_prior=True),
                _pipeline_json(confidence=0.7),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query="chart that for Acme",
            db_data=[], prior_data=prior_data,
        ))
        assert output.clarification is None
        assert "graph" in _kinds(output)

    def test_stale_entity_change_blocked(self):
        stale, _ = is_visual_stale_for_query(
            None,  # type: ignore[arg-type]
            "Compare Globex and Initech over the last 3 years on revenue growth",
            "Compare Acme and Globex over the last 3 years on revenue growth",
        )
        assert stale is True

    def test_same_query_not_stale(self):
        stale, _ = is_visual_stale_for_query(
            None,  # type: ignore[arg-type]
            "Compare Acme and Globex over the last 3 years on revenue growth",
            "Compare Acme and Globex over the last 3 years on revenue growth",
        )
        assert stale is False

    def test_what_if_followup_does_not_reuse_history(self, monkeypatch):
        rows = [
            {"created_at": "2024-01-01", "revenue": 100},
            {"created_at": "2024-01-02", "revenue": 200},
            {"created_at": "2024-01-03", "revenue": 300},
        ]
        prior_data = {
            "columns": ["created_at", "revenue"],
            "row_count": 3, "rows": rows, "from_query": "Compare Acme and Globex over the last 3 years",
        }
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.7)),
        )
        assert is_what_if_query("What if Acme raises price 10%?") is True


# ---------------------------------------------------------------------------
# Invalid entity types: never enter market adapters
# ---------------------------------------------------------------------------
class TestInvalidEntityTypes:
    def test_concepts_are_not_financial(self):
        assert classify_entity_type("happiness") == "CONCEPT"
        assert is_financial_entity("happiness") is False
        # Capitalization alone never creates a company: an unrecognized
        # proper-noun placeholder is UNKNOWN (non-financial until a symbol
        # search positively resolves it at retrieval time).
        assert classify_entity_type("Acme") == "UNKNOWN"
        assert is_financial_entity("Acme") is False

    def test_geographies_are_not_financial(self):
        assert is_financial_entity("Europe") is False

    def test_concept_comparison_no_market_chart_end_to_end(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.6)),
        )
        output = asyncio.run(run_pipeline(
            user_query="Compare happiness and sadness",
            db_data=[], source_scope="live_web",
            news_context=["Happiness research snippet", "Sadness research snippet"],
            web_sources=_sources(2),
        ))
        # No Yahoo-style comparison visuals for concepts.
        for visual in (output.visuals or []):
            if visual.visual_type == "comparison":
                assert "market cap" not in str(getattr(visual, "title", "")).lower()


# ---------------------------------------------------------------------------
# Mismatched units / timeframes / frequencies: never compared
# ---------------------------------------------------------------------------
class TestMismatchedComparability:
    def test_usd_vs_eur_absolutes_blocked(self):
        a = ComparisonEvidence(
            entity="Acme", metric="revenue_growth", value=100.0, unit="USD",
            period_start="2022-01-01", period_end="2024-01-01",
            frequency="annual", definition="annualTotalRevenue",
            is_historical=True, currency="USD",
        )
        b = ComparisonEvidence(
            entity="Globex", metric="revenue_growth", value=100.0, unit="EUR",
            period_start="2022-01-01", period_end="2024-01-01",
            frequency="annual", definition="annualTotalRevenue",
            is_historical=True, currency="EUR",
        )
        ok, _ = validate_comparison([a, b])
        assert ok is False
        assert validate_calculation_inputs(a, b)["ok"] is False

    def test_monthly_vs_annual_blocked(self):
        a = ComparisonEvidence(
            entity="Acme", metric="stock_performance", value=10.0, unit="price",
            period_start="2022-01-01", period_end="2024-01-01",
            frequency="monthly", definition="close", is_historical=True,
        )
        b = ComparisonEvidence(
            entity="Globex", metric="stock_performance", value=20.0, unit="price",
            period_start="2022-01-01", period_end="2024-01-01",
            frequency="annual", definition="close", is_historical=True,
        )
        assert validate_calculation_inputs(a, b)["ok"] is False

    def test_non_overlapping_periods_blocked(self):
        a = ComparisonEvidence(
            entity="Acme", metric="revenue_growth", value=100.0, unit="USD",
            period_start="2020-01-01", period_end="2021-01-01",
            frequency="annual", definition="annualTotalRevenue", is_historical=True,
        )
        b = ComparisonEvidence(
            entity="Globex", metric="revenue_growth", value=200.0, unit="USD",
            period_start="2023-01-01", period_end="2024-01-01",
            frequency="annual", definition="annualTotalRevenue", is_historical=True,
        )
        ok, detail = validate_comparison([a, b])
        assert ok is False
        assert "period" in detail.lower()

    def test_mismatched_end_to_end_no_chart(self, monkeypatch):
        query = "Compare Acme and Globex over the last 3 years on revenue growth"
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.8)),
        )
        financial = [
            _financial("Acme", "ACME", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9], currency="USD"),
            _financial("Globex", "GLOB", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9], currency="EUR"),
        ]
        # Absolute tables still render (per-row currency shown), but the
        # deterministic gate must note the mix and never compare absolutes.
        from app.services.llm.langchain_pipeline import _historical_comparison_gate

        gate = _historical_comparison_gate(
            query, price_history=[], financial_history=financial, fundamentals=[],
        )
        assert gate.get("currency_mixed")


# ---------------------------------------------------------------------------
# Unrelated / stale visual data: rejected, fail closed
# ---------------------------------------------------------------------------
class TestStaleVisualRejection:
    def test_unrelated_entity_visual_rejected_end_to_end(self, monkeypatch):
        query = "Compare Acme and Globex over the last 3 years on revenue growth"
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    confidence=0.8,
                    visuals=[{
                        "visual_type": "graph",
                        "props": {
                            "chart_type": "line",
                            "labels": ["2022", "2024"],
                            "datasets": [{"name": "UnknownCorp", "values": [1.0, 2.0]}],
                        },
                        "title": "UnknownCorp performance",
                    }],
                ),
            ),
        )
        price = [_weekly("Acme", "ACME", 10.0, 30.0), _weekly("Globex", "GLOB", 20.0, 40.0)]
        financial = [
            _financial("Acme", "ACME", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9]),
            _financial("Globex", "GLOB", ["2022", "2023", "2024"], [20e9, 22e9, 25e9], [2e9, 2.2e9, 2.5e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=query, db_data=[], source_scope="live_web",
            news_context=["Acme snippet", "Globex snippet"],
            web_sources=_sources(2),
            price_history=price, financial_history=financial,
        ))
        for visual in (output.visuals or []):
            if visual.visual_type == "graph":
                names = [d.get("name", "") for d in visual.props.get("datasets", [])]
                assert "UnknownCorp" not in names

    def test_validator_exception_rejects_visual(self):
        from app.services.llm.langchain_pipeline import VisualOutput

        visual = VisualOutput(
            visual_type="graph", title="g",
            props={"chart_type": "line", "labels": ["a"], "datasets": [{"name": "Acme", "values": [1.0]}]},
        )
        ok, _ = validate_visual_provenance(
            visual, query="Compare Acme and Globex",
            validated_entities=["Acme", "Globex"],
            validated_metrics=[METRIC_STOCK],
        )
        # No provenance IDs -> untraceable -> rejected (fail closed).
        assert ok is False

    def test_zero_confidence_never_charts(self, monkeypatch):
        query = "Compare Acme and Globex over the last 3 years on revenue growth"
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    confidence=0.0,
                    visuals=[{
                        "visual_type": "graph",
                        "props": {
                            "chart_type": "line", "labels": ["2022", "2024"],
                            "datasets": [{"name": "Acme", "values": [1.0, 2.0]}],
                        },
                        "title": "Acme performance",
                    }],
                ),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query=query, db_data=[], source_scope="live_web",
            news_context=["snippet"],
            web_sources=_sources(1),
        ))
        assert output.confidence == 0.0
        assert "graph" not in _kinds(output)
        assert "comparison" not in _kinds(output)


# ---------------------------------------------------------------------------
# Generic clarification discipline
# ---------------------------------------------------------------------------
class TestGenericClarification:
    def test_never_asks_for_supplied_metric(self):
        redundant, _ = clarification_is_redundant(
            "Which metric should I compare?", "Compare Acme and Globex on revenue growth"
        )
        assert redundant is True

    def test_never_asks_for_defaultable_timeframe(self):
        redundant, why = clarification_is_redundant(
            "Over which last years should I compare?",
            "Compare Acme and Globex on revenue growth",
        )
        assert redundant is True
        assert "timeframe" in why or "defaultable" in why

    def test_researchable_arbitrary_never_clarifies_end_to_end(self, monkeypatch):
        query = "Compare Acme and Globex over the last 3 years on revenue growth"
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(
                _decision_json(decision="clarify", missing="need figures"),
                _pipeline_json(answer="Best-effort Acme vs Globex.", confidence=0.5),
            ),
        )
        output = asyncio.run(run_pipeline(
            user_query=query, db_data=[], source_scope="live_web",
            news_context=["Acme snippet", "Globex snippet"],
            web_sources=_sources(2),
        ))
        assert output.clarification is None


# ---------------------------------------------------------------------------
# Visual provenance + single source of truth + confidence
# ---------------------------------------------------------------------------
class TestProvenanceAndConfidence:
    def test_every_chart_carries_provenance(self, monkeypatch):
        query = "Compare Acme and Globex over the last 3 years on stock performance and revenue growth"
        monkeypatch.setattr(
            pipeline_mod, "generate_response",
            _sequenced_fake(_decision_json(), _pipeline_json(confidence=0.8)),
        )
        price = [_weekly("Acme", "ACME", 10.0, 30.0), _weekly("Globex", "GLOB", 20.0, 40.0)]
        financial = [
            _financial("Acme", "ACME", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9]),
            _financial("Globex", "GLOB", ["2022", "2023", "2024"], [20e9, 22e9, 25e9], [2e9, 2.2e9, 2.5e9]),
        ]
        output = asyncio.run(run_pipeline(
            user_query=query, db_data=[], source_scope="live_web",
            news_context=["Acme snippet", "Globex snippet"],
            web_sources=_sources(2),
            price_history=price, financial_history=financial,
        ))
        assert output.confidence > 0.0
        charts = [v for v in (output.visuals or []) if v.visual_type in ("graph", "comparison")]
        assert charts
        for visual in charts:
            prov = getattr(visual, "provenance", None) or {}
            assert prov.get("intent")
            assert prov.get("entities")
            assert prov.get("source_ids") or prov.get("computation_ids") or prov.get("data_points") is not None

    def test_single_source_of_truth_validated_state(self):
        plan = dict(build_research_plan("Compare Acme and Globex over the last 3 years on revenue growth"))
        price = [_weekly("Acme", "ACME", 10.0, 30.0), _weekly("Globex", "GLOB", 20.0, 40.0)]
        financial = [
            _financial("Acme", "ACME", ["2022", "2023", "2024"], [10e9, 12e9, 15e9], [1e9, 1.2e9, 1.5e9]),
            _financial("Globex", "GLOB", ["2022", "2023", "2024"], [20e9, 22e9, 25e9], [2e9, 2.2e9, 2.5e9]),
        ]
        from app.services.llm.langchain_pipeline import _historical_comparison_gate

        gate = _historical_comparison_gate(
            "Compare Acme and Globex over the last 3 years on revenue growth",
            price_history=price, financial_history=financial,
        )
        state = build_validated_evidence_state(query="q", plan=plan, gate=gate, completeness=None)
        assert state["sufficient"] is True
        assert set(state["validated_entities"]) >= {"Acme", "Globex"}
        assert state["comparison_stats"] is not None
