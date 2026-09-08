"""Regression tests for the Tesla/BYD/Toyota 3-year comparison failure.

Exact failing query:
"Compare Tesla, BYD, and Toyota over the last 3 years. Which company had
the highest revenue growth, strongest stock price performance, and best
profitability? Show the year-by-year statistics, calculate the percentage
changes, and explain the main reasons behind the differences."

Live failure mode:
1. The agent asked the USER to provide annual revenue / net income /
   profit margin figures (researchable public data).
2. After the clarification reply, only Tesla + BYD 3Y market history was
   retrieved -- Toyota missing, revenue missing, net income missing.
3. No comparison answer, confidence 0%, research stopped.

Root causes fixed (see code): entity budget of 2 dropped the third
company, no BYD/Toyota canonical resolution, Yahoo-only financials with
no fallback research, clarification allowed to request researchable data,
and profitability had no single comparable metric.

HTTP is faked at the helper seam throughout -- no network here.
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
    build_research_plan,
    clarification_asks_for_researchable_data,
    compute_comparison_stats,
    compute_net_margins,
    compute_pct_change,
    compute_yearly_stats,
    detect_entities,
    detect_metrics,
    detect_period_years,
    is_researchable_comparison,
    symbol_for_entity,
)
from app.services.llm.langchain_pipeline import (
    _historical_comparison_gate,
    _margin_table_visual,
    _market_graph_visual,
    _price_history_graph_visual,
    ensure_visuals,
    run_pipeline,
)
from app.services.web_search import (
    STOCK_ALIASES,
    _entities_missing_financials,
    _extract_financials_from_snippets,
    _fallback_entities_from_text,
    _union_entities,
)


QUERY = (
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


def _price(entity, symbol, start, end, n=40):
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


def _financial(entity, symbol, rev_labels, rev_values, inc_values):
    return {
        "entity": entity, "symbol": symbol,
        "frequency": "annual", "is_historical": True,
        "revenue": {
            "labels": list(rev_labels), "values": list(rev_values),
            "period_start": rev_labels[0], "period_end": rev_labels[-1],
            "metric": "annualTotalRevenue", "unit": "currency",
        },
        "net_income": {
            "labels": list(rev_labels), "values": list(inc_values),
            "period_start": rev_labels[0], "period_end": rev_labels[-1],
            "metric": "annualNetIncome", "unit": "currency",
        },
    }


def _sources(*titles):
    return [
        {"title": title, "url": f"https://example.com/{i}", "provider": "Yahoo Finance"}
        for i, title in enumerate(titles)
    ]


# ---------------------------------------------------------------------------
# Decomposition: 3 entities, 4 metric cues, 3-year period, research plan
# ---------------------------------------------------------------------------
class TestDecomposition:
    def test_all_three_entities_detected(self):
        entities = detect_entities(QUERY)
        assert "Tesla" in entities
        assert "BYD" in entities
        assert "Toyota" in entities

    def test_all_metrics_detected(self):
        metrics = detect_metrics(QUERY)
        assert METRIC_STOCK in metrics      # "stock price performance"
        assert METRIC_REVENUE in metrics    # "revenue growth"
        assert METRIC_PROFIT in metrics     # "profitability" + "net income"

    def test_three_year_period_detected(self):
        assert detect_period_years(QUERY) == 3

    def test_research_plan_covers_every_entity_and_metric(self):
        plan = build_research_plan(QUERY)
        assert plan["required_entities"] == ["Tesla", "BYD", "Toyota"]
        assert set(plan["required_metrics"]) == {
            METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT,
        }
        task_ids = {task["id"] for task in plan["tasks"]}
        # Tasks A-D: financial + stock history per company.
        for entity in ("Tesla", "BYD", "Toyota"):
            assert f"stock:{entity}" in task_ids
            assert f"revenue:{entity}" in task_ids
            assert f"profit:{entity}" in task_ids
        # Tasks E-I: explanation, normalize, validate, calculate, answer.
        for task_id in ("explain", "normalize", "validate", "calculate", "answer"):
            assert task_id in task_ids

    def test_query_is_researchable_not_ambiguous(self):
        assert is_researchable_comparison(QUERY) is True


# ---------------------------------------------------------------------------
# Entity resolution: Toyota must never disappear
# ---------------------------------------------------------------------------
class TestEntityResolution:
    def test_canonical_symbols(self):
        assert STOCK_ALIASES["Tesla"] == "TSLA"
        assert STOCK_ALIASES["BYD"] == "BYDDY"
        assert STOCK_ALIASES["Toyota"] == "TM"
        assert symbol_for_entity("Tesla") == "TSLA"
        assert symbol_for_entity("BYD") == "BYDDY"
        assert symbol_for_entity("Toyota") == "TM"

    def test_union_recovers_llm_dropped_toyota(self):
        # The live rewriter returned only 2 entities; the deterministic
        # union must restore the third.
        assert _union_entities(["Tesla", "BYD"], QUERY) == [
            "Tesla", "BYD", "Toyota",
        ]

    def test_fallback_keeps_all_three(self):
        found = _fallback_entities_from_text(QUERY)
        assert "Tesla" in found and "BYD" in found and "Toyota" in found

    def test_resolve_symbol_needs_no_network(self):
        async def scenario():
            import httpx

            async with httpx.AsyncClient() as client:
                return [
                    await web_search_mod._resolve_symbol(client, entity)
                    for entity in ("Tesla", "BYD", "Toyota")
                ]

        assert asyncio.run(scenario()) == ["TSLA", "BYDDY", "TM"]


# ---------------------------------------------------------------------------
# Tool routing: stock AND financial research for all three companies
# ---------------------------------------------------------------------------
class TestToolRouting:
    def _install(self, monkeypatch, *, financial=None, snippets=None):
        calls: list = []

        async def fake_rewrite(query, prior_clarification=None, company_name=None):
            # Simulate the live LLM drop: only 2 of 3 entities framed.
            return {
                "queries": ["Tesla BYD Toyota 3 year comparison"],
                "entities": ["Tesla", "BYD"],
                "time_sensitive": False,
            }

        async def fake_resolve(client, entity):
            return {"Tesla": "TSLA", "BYD": "BYDDY", "Toyota": "TM"}.get(entity)

        async def fake_history(client, entity, symbol, years=3):
            calls.append(("stock", entity, symbol))
            starts = {"Tesla": 250.0, "BYD": 65.0, "Toyota": 150.0}
            ends = {"Tesla": 350.0, "BYD": 60.0, "Toyota": 180.0}
            return (
                f"{entity} history",
                _price(entity, symbol, starts[entity], ends[entity]),
                {"title": entity, "url": "", "provider": "Yahoo Finance"},
            )

        async def fake_financial(client, entity, symbol, years=3):
            calls.append(("financial", entity, symbol))
            return financial(entity) if financial else None

        async def fake_snippets(client, query_item, settings, time_sensitive=False):
            return snippets(query_item) if snippets else []

        monkeypatch.setattr(
            web_search_mod, "rewrite_search_queries", fake_rewrite
        )
        monkeypatch.setattr(web_search_mod, "_resolve_symbol", fake_resolve)
        monkeypatch.setattr(web_search_mod, "_fetch_market_history", fake_history)
        monkeypatch.setattr(
            web_search_mod, "_fetch_financial_history", fake_financial
        )
        monkeypatch.setattr(web_search_mod, "_snippets_for_query", fake_snippets)
        monkeypatch.setattr(web_search_mod, "_fetch_wikipedia", _no_wiki)
        monkeypatch.setattr(web_search_mod.get_settings(), "WEB_SEARCH_API_KEY", None)
        monkeypatch.setattr(web_search_mod.get_settings(), "FRED_API_KEY", None)
        return calls

    def test_stock_and_financial_research_for_all_three(self, monkeypatch):
        def financial(entity):
            return (
                f"{entity} financials",
                _financial(
                    entity, "X",
                    ["2022", "2023", "2024", "2025"],
                    [100.0, 110.0, 120.0, 130.0],
                    [10.0, 11.0, 12.0, 13.0],
                ),
                {"title": entity, "url": "", "provider": "Yahoo Finance"},
            )

        calls = self._install(monkeypatch, financial=financial)
        result = asyncio.run(web_search_mod.search_web(QUERY, planned_tools=[]))
        stock_entities = {call[1] for call in calls if call[0] == "stock"}
        financial_entities = {call[1] for call in calls if call[0] == "financial"}
        # Entity-complete: Toyota researched despite the LLM drop.
        assert stock_entities == {"Tesla", "BYD", "Toyota"}
        assert financial_entities == {"Tesla", "BYD", "Toyota"}
        assert {item["entity"] for item in result.price_history} == {
            "Tesla", "BYD", "Toyota",
        }
        assert {item["entity"] for item in result.financial_history} == {
            "Tesla", "BYD", "Toyota",
        }

    def test_missing_yahoo_financials_trigger_snippet_research(self, monkeypatch):
        # Yahoo financial endpoint down for everyone: the second pass must
        # still recover annual series from targeted web research.
        def snippets(query_item):
            lowered = query_item.lower()
            if "tesla" in lowered:
                return [
                    (
                        "Tesla revenue was $96.8 billion in FY2023, up from "
                        "$81.5 billion in FY2022. Tesla net income was $15.0 "
                        "billion in FY2023 versus $12.6 billion in FY2022.",
                        "https://t.example/tesla", "Tavily", "2024-02-01", 0.9,
                    ),
                ]
            if "byd" in lowered:
                return [
                    (
                        "BYD revenue was 602.3 billion yuan in 2023, up from "
                        "424.1 billion yuan in 2022. BYD net income was 30.0 "
                        "billion yuan in 2023 versus 16.6 billion yuan in 2022.",
                        "https://t.example/byd", "Tavily", "2024-03-01", 0.9,
                    ),
                ]
            if "toyota" in lowered:
                return [
                    (
                        "Toyota revenue was 45.1 trillion yen in FY2024, up from "
                        "37.2 trillion yen in FY2023. Toyota net income was 4.9 "
                        "trillion yen in FY2024 versus 2.5 trillion yen in FY2023.",
                        "https://t.example/toyota", "Tavily", "2024-05-01", 0.9,
                    ),
                ]
            return []

        calls = self._install(
            monkeypatch, financial=None, snippets=snippets
        )
        result = asyncio.run(web_search_mod.search_web(QUERY, planned_tools=[]))
        assert {call[1] for call in calls if call[0] == "financial"} == {
            "Tesla", "BYD", "Toyota"
        }
        # Second pass recovered all three despite Yahoo failing.
        assert {item["entity"] for item in result.financial_history} == {
            "Tesla", "BYD", "Toyota"
        }
        assert any("Second-pass" in note for note in result.research_notes)

    def test_stock_history_alone_partial_for_stock_only(self):
        gate = _historical_comparison_gate(
            QUERY,
            price_history=[
                _price("Tesla", "TSLA", 250.0, 350.0),
                _price("BYD", "BYDDY", 65.0, 60.0),
                _price("Toyota", "TM", 150.0, 180.0),
            ],
            financial_history=[],
        )
        assert gate["applies"] is True
        # Partial-metric policy: stock validates (all three), revenue/profit
        # excluded -- sufficient for a stock-only partial, not a full block.
        assert gate["blocked"] is False
        assert gate.get("partial") is True
        assert gate.get("validated_metrics") == ["stock_performance"] or "stock_performance" in gate.get("validated_metrics", [])


async def _no_wiki(client, entity):
    return None


# ---------------------------------------------------------------------------
# Snippet financial extraction
# ---------------------------------------------------------------------------
class TestSnippetFinancialExtraction:
    def test_extracts_revenue_and_income_with_years(self):
        triples = [
            (
                "Tesla revenue was $96.8 billion in FY2023, up from $81.5 "
                "billion in FY2022. Tesla net income was $15.0 billion in "
                "FY2023 versus $12.6 billion in FY2022.",
                "https://t.example/tesla", "Tavily", "2024-02-01", 0.9,
            ),
        ]
        extracted = _extract_financials_from_snippets("Tesla", triples, years=3)
        assert extracted is not None
        _text, payload, _source = extracted
        assert payload["revenue"]["values"] == [81500000000.0, 96800000000.0]
        assert payload["net_income"]["values"] == [12600000000.0, 15000000000.0]
        # Fiscal labels preserved, not normalized away.
        assert "FY2023" in payload["revenue"]["labels"]

    def test_unattributed_or_undated_money_ignored(self):
        assert _extract_financials_from_snippets("Toyota", [], years=3) is None
        assert _extract_financials_from_snippets(
            "Toyota",
            [("Someone raised $50M last year.", "https://x.example", "Tavily", None, None)],
            years=3,
        ) is None
        # Single year only: growth undefined, no series.
        assert _extract_financials_from_snippets(
            "Toyota",
            [("Toyota revenue was 45 trillion yen in FY2024.", "https://x.example", "Tavily", None, None)],
            years=3,
        ) is None

    def test_stock_price_figures_rejected(self):
        extracted = _extract_financials_from_snippets(
            "Tesla",
            [(
                "Tesla stock price hit $248.50 on 2023-09-04 after revenue "
                "headlines. Tesla revenue was $96.8 billion in FY2023, up "
                "from $81.5 billion in FY2022.",
                "https://t.example", "Tavily", None, None,
            )],
            years=3,
        )
        assert extracted is not None
        # The $248.50 close must not leak into the revenue series.
        assert extracted[1]["revenue"]["values"] == [81500000000.0, 96800000000.0]


# ---------------------------------------------------------------------------
# Deterministic calculations
# ---------------------------------------------------------------------------
class TestDeterministicCalculations:
    def test_growth_formulas(self):
        assert compute_pct_change(100.0, 130.0) == 30.0
        assert compute_pct_change(250.0, 350.0) == 40.0

    def test_net_margin_formula(self):
        assert compute_net_margins([100.0, 200.0], [10.0, 50.0]) == [10.0, 25.0]
        assert compute_net_margins([0.0], [5.0]) == [None]

    def test_yearly_stats_preserve_labels_and_yoy(self):
        yearly = compute_yearly_stats(
            ["FY2022", "FY2023", "FY2024"], [100.0, 150.0, 120.0]
        )
        assert [point["label"] for point in yearly] == ["FY2022", "FY2023", "FY2024"]
        assert yearly[0]["yoy_pct_change"] is None
        assert yearly[1]["yoy_pct_change"] == 50.0
        assert yearly[2]["yoy_pct_change"] == -20.0

    def test_profitability_winner_is_highest_margin(self):
        stats = compute_comparison_stats(
            {
                "Tesla": {
                    METRIC_STOCK: (250.0, 350.0),
                    METRIC_REVENUE: (96000.0, 130000.0),
                    METRIC_PROFIT: (12600.0, 15000.0),
                },
                "BYD": {
                    METRIC_STOCK: (65.0, 60.0),
                    METRIC_REVENUE: (424000.0, 602000.0),
                    METRIC_PROFIT: (16600.0, 30000.0),
                },
                "Toyota": {
                    METRIC_STOCK: (150.0, 180.0),
                    METRIC_REVENUE: (37200000.0, 45100000.0),
                    METRIC_PROFIT: (2500000.0, 4900000.0),
                },
            },
            latest_margins={"Tesla": 15.5, "BYD": 5.0, "Toyota": 10.9},
        )
        # BYD has the highest net-INCOME growth but Tesla the best margin.
        assert stats["entities"]["BYD"][METRIC_PROFIT]["pct_change"] > (
            stats["entities"]["Tesla"][METRIC_PROFIT]["pct_change"]
        )
        assert stats["winners"][METRIC_PROFIT] == "Tesla"
        assert stats["winners"][METRIC_STOCK] == "Tesla"
        assert stats["winners"][METRIC_REVENUE] == "BYD"
        assert "net profit margin" in stats["profitability_definition"]


# ---------------------------------------------------------------------------
# No ask-user-for-data clarification loop
# ---------------------------------------------------------------------------
class TestNoClarificationForResearchableData:
    def test_data_request_question_detected(self):
        assert clarification_asks_for_researchable_data(
            "Please provide annual revenue, net income, and profit margin "
            "figures for each company for FY2021-FY2023.",
            QUERY,
        ) is True

    def test_genuine_ambiguity_not_suppressed(self):
        assert clarification_asks_for_researchable_data(
            "Which quarter did you mean?", "how did Q1 go?"
        ) is False

    def test_pipeline_suppresses_data_request_clarification(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    answer="",
                    confidence=0.0,
                    clarification={
                        "question": "Please provide annual revenue, net "
                        "income, and profit margin figures for each company.",
                        "options": [],
                    },
                ),
                _pipeline_json(
                    answer="Researched best-effort answer with missing data stated.",
                    confidence=0.4,
                ),
            ),
        )
        output = asyncio.run(
            run_pipeline(
                user_query=QUERY,
                db_data=[],
                source_scope="live_web",
                news_context=["snippet one", "snippet two"],
                web_sources=_sources("s1", "s2"),
            )
        )
        assert output.clarification is None
        assert "Researched best-effort" in output.answer
        assert any("Suppressed" in step for step in output.thinking)


# ---------------------------------------------------------------------------
# Evidence gate: incomplete evidence, entity completeness, visuals
# ---------------------------------------------------------------------------
class TestEvidenceGate:
    def test_two_of_three_partial_with_exclusion(self):
        gate = _historical_comparison_gate(
            QUERY,
            price_history=[
                _price("Tesla", "TSLA", 250.0, 350.0),
                _price("BYD", "BYDDY", 65.0, 60.0),
            ],
            financial_history=[
                _financial("Tesla", "TSLA", ["2022", "2023"], [90.0, 96.0], [12.0, 15.0]),
                _financial("BYD", "BYDDY", ["2022", "2023"], [424.0, 602.0], [16.0, 30.0]),
            ],
        )
        # Partial-result policy: 2-of-3 validated subset is sufficient.
        assert gate["blocked"] is False
        assert gate.get("partial") is True
        assert "Toyota" in str(gate.get("excluded_by_metric", {}))

    def test_partial_market_graph_never_charts_for_three(self):
        graph = _market_graph_visual(
            [
                {"entity": "Tesla", "labels": ["2023-01-01", "2024-01-01"],
                 "values": [250.0, 350.0]},
                {"entity": "BYD", "labels": ["2023-01-01", "2024-01-01"],
                 "values": [65.0, 60.0]},
            ],
            QUERY,
        )
        assert graph is None

    def test_incomplete_evidence_yields_partial_stock_chart(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(answer="thin", confidence=0.7)
            ),
        )
        output = asyncio.run(
            run_pipeline(
                user_query=QUERY,
                db_data=[],
                source_scope="live_web",
                news_context=["snippet one"],
                price_history=[
                    _price("Tesla", "TSLA", 250.0, 350.0),
                    _price("BYD", "BYDDY", 65.0, 60.0),
                ],
                financial_history=[],
                web_sources=_sources("s1"),
            )
        )
        # Partial: stock subset (Tesla/BYD) validates; revenue/profit excluded.
        assert output.confidence > 0.0
        assert output.confidence <= 0.65
        assert "toyota" in (output.answer or "").lower()
        # No zero-filled Toyota values in any visual.
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
            if visual.visual_type == "graph":
                names = [d.get("name", "") for d in visual.props.get("datasets", [])]
                assert "Toyota" not in names

    def test_full_evidence_charts_all_three(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(answer="solid", confidence=0.8)
            ),
        )
        prices = [
            _price("Tesla", "TSLA", 250.0, 350.0),
            _price("BYD", "BYDDY", 65.0, 60.0),
            _price("Toyota", "TM", 150.0, 180.0),
        ]
        financials = [
            _financial("Tesla", "TSLA", ["FY2022", "FY2023", "FY2024", "FY2025"],
                       [81.5, 96.8, 110.0, 130.0], [12.6, 15.0, 16.0, 20.0]),
            _financial("BYD", "BYDDY", ["2022", "2023", "2024", "2025"],
                       [424.1, 602.3, 700.0, 800.0], [16.6, 30.0, 35.0, 40.0]),
            _financial("Toyota", "TM", ["FY2022", "FY2023", "FY2024", "FY2025"],
                       [37.2, 40.0, 45.1, 48.0], [2.5, 3.0, 4.9, 5.5]),
        ]
        output = asyncio.run(
            run_pipeline(
                user_query=QUERY,
                db_data=[],
                source_scope="live_web",
                news_context=["Tesla snippet", "BYD snippet", "Toyota snippet"],
                price_history=prices,
                financial_history=financials,
                web_sources=_sources("s1", "s2", "s3"),
            )
        )
        assert output.confidence > 0.0
        graphs = [v for v in output.visuals if v.visual_type == "graph"]
        assert graphs
        names = [dataset["name"] for dataset in graphs[0].props["datasets"]]
        assert names == ["Tesla", "BYD", "Toyota"]
        tables = [v for v in output.visuals if v.visual_type == "table"]
        table_text = json.dumps([table.props for table in tables])
        assert "Tesla" in table_text and "BYD" in table_text and "Toyota" in table_text
        # Profitability compared on ONE metric for every company.
        assert any("margin" in table.title.lower() for table in tables)

    def test_margin_table_single_comparable_metric(self):
        table = _margin_table_visual([
            _financial("Tesla", "TSLA", ["FY2022", "FY2023"], [81.5, 96.8], [12.6, 15.0]),
            _financial("BYD", "BYDDY", ["2022", "2023"], [424.1, 602.3], [16.6, 30.0]),
            _financial("Toyota", "TM", ["FY2022", "FY2023"], [37.2, 40.0], [2.5, 3.0]),
        ])
        assert table is not None
        flat = json.dumps(table.props["values"])
        assert "Tesla" in flat and "BYD" in flat and "Toyota" in flat
        assert _margin_table_visual([
            _financial("Tesla", "TSLA", ["FY2022"], [81.5], [12.6]),
        ]) is None

    def test_gate_stats_carry_yearly_margins_and_basis(self):
        gate = _historical_comparison_gate(
            QUERY,
            price_history=[
                _price("Tesla", "TSLA", 250.0, 350.0),
                _price("BYD", "BYDDY", 65.0, 60.0),
                _price("Toyota", "TM", 150.0, 180.0),
            ],
            financial_history=[
                _financial("Tesla", "TSLA", ["FY2022", "FY2023"], [81.5, 96.8], [12.6, 15.0]),
                _financial("BYD", "BYDDY", ["2022", "2023"], [424.1, 602.3], [16.6, 30.0]),
                _financial("Toyota", "TM", ["FY2022", "FY2023"], [37.2, 40.0], [2.5, 3.0]),
            ],
        )
        assert gate["blocked"] is False
        stats = gate["comparison_stats"]
        assert set(stats["winners"]) == {
            METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT,
        }
        assert stats["latest_margins"]["Tesla"] == round(15.0 / 96.8 * 100, 2)
        assert stats["yearly"]["Toyota"]["revenue"][0]["label"] == "FY2022"
        assert stats["yearly"]["BYD"]["revenue"][1]["yoy_pct_change"] == round(
            (602.3 - 424.1) / 424.1 * 100, 2
        )
        assert "Fiscal-year labels as reported" in stats["period_basis"]
