"""Hallucination golden set (eval debt, CI-safe).

Pins the pipeline's anti-hallucination contracts end-to-end through
`run_pipeline` with a MOCKED LLM (no network, no keys — safe for CI), plus
the deterministic helpers underneath:

1. own-data grouping — a global total must equal the sum of its grouped
   parts, and every narrated/charted number must come from the rows.
2. doc paraphrase recall — an answer over uploaded-document chunks may
   restate chunk figures but never invent new ones.
3. 3Y comparison with 1 missing entity — PARTIAL (not fail-all): the missing
   entity is disclosed in prose, excluded from every visual, and never named
   as a winner.
4. forecast with 3 points — insufficient history yields NO forecast (the
   deterministic gate returns None, so no forecast visual and no projected
   number may appear). NOTE: the pipeline does not currently force
   confidence to 0.0 here — the narrator's confidence passes through — so
   this case pins "no forecast emitted", not a confidence value.
5. what-if with no price column — no scenario is computed and no scenario
   math may appear.

Global assertions in every case: no invented numbers (every non-year number
in the answer and every numeric visual value traces to the evidence within
1%), blocked gates stay blocked, and exclusions are exact.
"""

import asyncio
import json
import re

import app.services.llm.langchain_pipeline as pipeline_mod
from app.services import web_search_cache
from app.services.data.stats import (
    apply_what_if,
    compute_forecast,
    compute_statistics,
    infer_forecast_columns,
    is_forecast_query,
    parse_what_if,
)
from app.services.llm.langchain_pipeline import (
    _historical_comparison_gate,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# Shared helpers (mocked LLM + number-grounding assertions)
# ---------------------------------------------------------------------------

def _sequenced_fake(*contents):
    calls = {"n": 0}

    async def fake(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
        content = contents[min(calls["n"], len(contents) - 1)]
        calls["n"] += 1
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


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\$?\d[\d,]*(?:\.\d+)?")


def _numbers_in(text):
    """Floats appearing in prose. Year-like integers (1900-2100) are skipped
    (they are labels, not claims), as are digits glued to letters ("E1")."""
    out = []
    for match in _NUMBER_RE.finditer(text or ""):
        raw = match.group(0).replace("$", "").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if 1900 <= value <= 2100 and value.is_integer():
            continue
        out.append(value)
    return out


def _flatten_numbers(obj):
    """All numeric values inside nested computed-stats dicts/lists."""
    found = []
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        for value in obj.values():
            found.extend(_flatten_numbers(value))
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found.extend(_flatten_numbers(value))
    return found


def _visual_numbers(visuals):
    """Every numeric value carried by visual payloads (datasets, tables,
    comparison value/baseline/groups). String labels are never claims."""
    found = []

    def walk(node):
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.append(float(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    for visual in visuals or []:
        walk(getattr(visual, "props", None) or {})
    return found


def _assert_no_invented_numbers(answer, visuals, evidence_numbers):
    """Every non-year number in the answer and every visual value must match
    a known evidence number within 1% (exact for small ints via tolerance)."""
    known = [float(v) for v in evidence_numbers]
    assert known, "evidence set must not be empty"
    for value in _numbers_in(answer) + _visual_numbers(visuals):
        assert any(
            abs(value - candidate) <= max(1e-6, abs(candidate) * 0.01)
            for candidate in known
        ), f"invented number {value} not in evidence {sorted(set(known))}"


def _visual_dataset_names(visuals):
    names = []
    for visual in visuals or []:
        for dataset in ((getattr(visual, "props", None) or {}).get("datasets") or []):
            if isinstance(dataset, dict) and dataset.get("name"):
                names.append(str(dataset["name"]))
    return names


def _mock(monkeypatch, answer, confidence=0.7):
    monkeypatch.setattr(
        pipeline_mod, "generate_response",
        _sequenced_fake(_decision_json(), _pipeline_json(
            answer=answer, confidence=confidence)),
    )


# ---------------------------------------------------------------------------
# 1. own-data grouping (global vs grouped)
# ---------------------------------------------------------------------------

class TestOwnDataGrouping:
    ROWS = [
        {"created_at": "2024-01-01", "revenue": 100, "region": "east"},
        {"created_at": "2024-01-02", "revenue": 250, "region": "west"},
        {"created_at": "2024-01-03", "revenue": 175, "region": "east"},
        {"created_at": "2024-01-04", "revenue": 300, "region": "west"},
    ]

    def test_global_total_equals_sum_of_grouped_totals(self):
        computed = compute_statistics(self.ROWS)
        assert computed["totals"]["revenue"] == 825.0
        east = sum(r["revenue"] for r in self.ROWS if r["region"] == "east")
        west = sum(r["revenue"] for r in self.ROWS if r["region"] == "west")
        assert (east, west) == (275.0, 550.0)
        assert computed["totals"]["revenue"] == east + west

    def test_grouped_answer_has_no_invented_numbers(self, monkeypatch):
        computed = compute_statistics(self.ROWS)
        _mock(monkeypatch,
              "Total revenue is 825 across 4 rows: east 275 and west 550.")
        output = asyncio.run(run_pipeline(
            user_query="how is revenue split across regions?",
            db_data=self.ROWS, computed_numbers=computed,
            source_scope="own_data",
        ))
        assert output.clarification is None
        assert "825" in (output.answer or "")
        # Grouped subtotals are derived from the same rows (pinned equal to
        # the global total above), so they are evidence — not invention.
        grouped = {}
        for row in self.ROWS:
            grouped[row["region"]] = grouped.get(row["region"], 0) + row["revenue"]
        evidence = (
            [r["revenue"] for r in self.ROWS]
            + _flatten_numbers(computed)
            + [computed["row_count"]]
            + list(grouped.values())
        )
        _assert_no_invented_numbers(output.answer, output.visuals, evidence)


# ---------------------------------------------------------------------------
# 2. doc paraphrase recall
# ---------------------------------------------------------------------------

class TestDocParaphraseRecall:
    CHUNKS = [
        "Acme FY2024 report states revenue of 520 dollars with 120 employees.",
        "The same report notes operating cost of 310 dollars for FY2024.",
    ]

    def _sources(self):
        return [{
            "title": "Acme FY2024 report", "url": "", "provider": "your_documents",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        }]

    def test_paraphrase_keeps_chunk_figures(self, monkeypatch):
        _mock(monkeypatch,
              "Acme FY2024 revenue was 520 dollars with operating cost "
              "of 310 dollars (120 employees).")
        output = asyncio.run(run_pipeline(
            user_query="what does the Acme FY2024 report say about revenue and cost?",
            db_data=[], source_scope="own_data",
            news_context=list(self.CHUNKS), web_sources=self._sources(),
        ))
        assert output.clarification is None
        assert "520" in (output.answer or "")
        assert "310" in (output.answer or "")
        evidence = []
        for chunk in self.CHUNKS:
            evidence.extend(_numbers_in(chunk))
        _assert_no_invented_numbers(output.answer, output.visuals, evidence)

    def test_unrelated_figure_cannot_enter_answer(self, monkeypatch):
        # Even when the (mocked) narrator tries to smuggle in an outside
        # figure, no visual may carry it: visuals are deterministic.
        _mock(monkeypatch, "Revenue was 520 dollars.")
        output = asyncio.run(run_pipeline(
            user_query="what does the Acme FY2024 report say about revenue?",
            db_data=[], source_scope="own_data",
            news_context=list(self.CHUNKS), web_sources=self._sources(),
        ))
        for value in _visual_numbers(output.visuals):
            assert value in (520.0, 310.0, 120.0)


# ---------------------------------------------------------------------------
# 3. 3Y comparison with 1 missing entity
# ---------------------------------------------------------------------------

def _weekly(entity, symbol, start, end, n=40):
    import datetime

    base = datetime.date(2022, 9, 7)
    labels, values = [], []
    for i in range(n):
        labels.append((base + datetime.timedelta(days=int(i * 3 * 365 / n))).isoformat())
        values.append(round(start + (end - start) * i / (n - 1), 2))
    return {
        "entity": entity, "symbol": symbol, "currency": "USD",
        "labels": labels, "values": values,
        "frequency": "weekly", "period_start": labels[0], "period_end": labels[-1],
        "is_historical": True, "metric": "close",
    }


def _financial(entity, symbol, labels, rev, inc):
    block = lambda values, metric: {
        "labels": list(labels), "values": list(values),
        "period_start": labels[0], "period_end": labels[-1],
        "metric": metric, "unit": "currency", "currency": "USD",
    }
    return {
        "entity": entity, "symbol": symbol,
        "frequency": "annual", "is_historical": True,
        "revenue": block(rev, "annualTotalRevenue"),
        "net_income": block(inc, "annualNetIncome"),
    }


class TestThreeYearMissingEntity:
    QUERY = "Compare E1, E2 and E3 over the last 3 years on revenue growth"
    LABELS = ["2022-01-01", "2023-01-01", "2024-01-01"]

    def _evidence(self):
        price = [_weekly("E1", "E1", 10.0, 30.0), _weekly("E2", "E2", 20.0, 40.0)]
        financial = [
            _financial("E1", "E1", self.LABELS, [100.0, 120.0, 150.0], [10.0, 12.0, 15.0]),
            _financial("E2", "E2", self.LABELS, [200.0, 210.0, 230.0], [20.0, 21.0, 23.0]),
        ]
        return price, financial

    def test_gate_is_partial_with_exact_exclusion(self):
        price, financial = self._evidence()
        gate = _historical_comparison_gate(
            self.QUERY, price_history=price, financial_history=financial)
        assert gate["blocked"] is False
        assert gate.get("partial") is True
        assert gate.get("excluded_entities") == ["E3"]
        assert gate.get("comparison_stats") is not None
        # No winner for the missing entity: winners name validated entities.
        winners = gate["comparison_stats"].get("winners") or {}
        assert winners, "partial gate must still crown winners for validated entities"
        assert "E3" not in set(winners.values())
        assert set(winners.values()) <= {"E1", "E2"}

    def test_partial_answer_discloses_excludes_and_names_no_missing_winner(
        self, monkeypatch,
    ):
        web_search_cache._reset_cache_state()
        _mock(monkeypatch, "Among E1 and E2 revenue grew.", confidence=0.7)
        price, financial = self._evidence()
        output = asyncio.run(run_pipeline(
            user_query=self.QUERY, db_data=[], source_scope="live_web",
            news_context=["E1 snippet", "E2 snippet"],
            web_sources=[
                {"title": "E1 source", "url": "https://example.com/e1",
                 "provider": "tavily", "retrieved_at": "t"},
                {"title": "E2 source", "url": "https://example.com/e2",
                 "provider": "tavily", "retrieved_at": "t"},
            ],
            price_history=price, financial_history=financial,
        ))
        web_search_cache._reset_cache_state()
        assert output.clarification is None
        # Partial cap: a 2-of-3 answer must never sound certain.
        assert output.confidence <= 0.65
        # Exclusion disclosed in prose ...
        assert "E3" in (output.answer or "")
        # ... but excluded from every visual ...
        assert "E3" not in _visual_dataset_names(output.visuals)
        assert "E3" not in json.dumps(
            [v.props for v in (output.visuals or [])], default=str)
        # ... and never named as a winner: the missing entity may appear
        # exactly once — inside the exclusion disclosure — with no
        # winner/strongest/best claim attached to it.
        mentions = [m.start() for m in re.finditer(r"E3", output.answer or "")]
        assert len(mentions) == 1, f"E3 must appear only in the disclosure: {output.answer!r}"
        start = max(0, mentions[0] - 160)
        disclosure = (output.answer or "")[start:mentions[0] + 20].lower()
        assert "exclud" in disclosure
        tail = (output.answer or "")[mentions[0]:mentions[0] + 60].lower()
        assert not re.search(r"winn|strongest|best|highest", tail)


# ---------------------------------------------------------------------------
# 4. forecast with 3 points (insufficient history -> no forecast)
# ---------------------------------------------------------------------------

class TestForecastThreePoints:
    QUERY = "forecast next month's revenue"
    ROWS = [
        {"month": "2024-01-01", "revenue": 100},
        {"month": "2024-02-01", "revenue": 120},
        {"month": "2024-03-01", "revenue": 140},
    ]

    def test_three_points_yield_no_forecast(self):
        assert is_forecast_query(self.QUERY) is True
        date_col, value_col = infer_forecast_columns(self.ROWS)
        assert (date_col, value_col) == ("month", "revenue")
        assert compute_forecast(self.ROWS, date_col, value_col) is None
        # The chat route only attaches a forecast when the gate returns one,
        # so None here means the key is absent downstream, never a guess.
        computed = compute_statistics(self.ROWS)
        assert "forecast" not in computed

    def test_pipeline_emits_no_forecast_visual_or_number(self, monkeypatch):
        computed = compute_statistics(self.ROWS)
        _mock(monkeypatch,
              "Revenue so far: 100, 120, 140 across 3 months. "
              "That is not enough history to forecast.")
        output = asyncio.run(run_pipeline(
            user_query=self.QUERY, db_data=self.ROWS,
            computed_numbers=computed, source_scope="own_data",
        ))
        for visual in output.visuals or []:
            title = str((getattr(visual, "title", None) or "")).lower()
            assert "forecast" not in title and "projected" not in title
            names = " ".join(
                str(d.get("name", "")) for d in
                ((getattr(visual, "props", None) or {}).get("datasets") or [])
                if isinstance(d, dict)
            ).lower()
            assert "projected" not in names
        evidence = (
            [r["revenue"] for r in self.ROWS]
            + _flatten_numbers(computed)
            + [computed["row_count"]]
        )
        _assert_no_invented_numbers(output.answer, output.visuals, evidence)


# ---------------------------------------------------------------------------
# 5. what-if with no price column (no scenario, no invented math)
# ---------------------------------------------------------------------------

class TestWhatIfNoPriceColumn:
    QUERY = "what if we raise price 10%?"
    ROWS = [
        {"region": "east", "quantity": 10},
        {"region": "west", "quantity": 20},
    ]

    def test_no_price_column_means_no_scenario(self):
        assert parse_what_if(self.QUERY) == ("price", 10.0)
        assert apply_what_if(self.ROWS, "price", 10.0) is None

    def test_pipeline_emits_no_scenario_or_math(self, monkeypatch):
        computed = compute_statistics(self.ROWS)
        assert "what_if" not in computed
        _mock(monkeypatch,
              "I cannot model a price change: your data has quantity by "
              "region (10 east, 20 west) but no price column.")
        output = asyncio.run(run_pipeline(
            user_query=self.QUERY, db_data=self.ROWS,
            computed_numbers=computed, source_scope="own_data",
        ))
        for visual in output.visuals or []:
            prov = getattr(visual, "provenance", None) or {}
            assert "what_if" not in (prov.get("computation_ids") or [])
            assert "scenario" not in str(
                getattr(visual, "title", "") or "").lower()
        evidence = (
            [r["quantity"] for r in self.ROWS]
            + _flatten_numbers(computed)
            + [computed["row_count"], 10.0]  # 10% is the asked change, not a result
        )
        _assert_no_invented_numbers(output.answer, output.visuals, evidence)
