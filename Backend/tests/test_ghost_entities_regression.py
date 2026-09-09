"""Regression tests for ghost-entity detection (live-web comparison garbage).

Pins detect_entities() output for the four production repro queries verbatim:

- Case A (US/UK macro): countries + macro topics must yield ZERO entities,
  so the multi-company comparison gate cannot fire on them.
- Case B (Shopify/WooCommerce/BigCommerce): Oxford-comma list must parse
  to three clean entities -- never "And BigCommerce".
- Case C (SaaS what-if calculator): sentence words ("My", "If") and
  result language ("Projected Results Visually") must yield ZERO entities.
- Case D (OpenAI/Anthropic/Google DeepMind/Meta AI): CamelCase names must
  be detected whole ("OpenAI", not ghost fragment "AI"); the "Meta AI"
  mention must resolve to one entity, not "META" + "AI".

Pure unit tests -- no network, no LLM.
"""

from app.services.data.comparison import (
    classify_entity_type,
    detect_entities,
    decompose_comparison_query,
    may_attempt_market_resolution,
)
from app.services.web_search import (
    _fallback_entities_from_text,
    _quote_matches_entity,
    _resolve_symbol,
)

QUERY_A = (
    "How has inflation affected housing affordability in the US and UK from "
    "2020 to 2025? Compare inflation, house-price growth, and mortgage rates "
    "year by year, and show the trends visually."
)
QUERY_B = (
    "Compare Shopify, WooCommerce, and BigCommerce over the last 5 years "
    "on revenue, profitability and stock performance."
)
QUERY_C = (
    "My SaaS has 5,000 customers paying $100/month. If I increase the price "
    "by 25%, lose 18% of customers, and reduce operating costs by 12%, "
    "calculate the new monthly revenue, revenue percentage change, and "
    "estimated operating-cost impact. Show baseline vs projected results "
    "visually."
)
QUERY_D = (
    "Compare OpenAI, Anthropic, Google DeepMind, and Meta AI on funding, "
    "model releases, and estimated market impact over the last 3 years."
)
# Case E (production screenshot): imperative verbs at sentence starts
# ("Model three scenarios", "Keep pricing constant") ghosted as entities
# with bogus "Wikipedia: Model" / "Wikipedia: Keep" sources attached.
QUERY_E = (
    "My SaaS currently has 8,000 customers paying $75/month. Model three "
    "scenarios for next month: conservative with 5% customer growth and "
    "10% churn, base with 10% growth and 5% churn, and aggressive with "
    "20% growth and 3% churn. Keep pricing constant, calculate revenue "
    "for each scenario, rank them, and show the sensitivity visually."
)


class TestGhostEntities:
    def test_case_a_macro_countries_yield_no_entities(self):
        assert detect_entities(QUERY_A) == []

    def test_case_b_oxford_comma_has_no_and_entity(self):
        assert detect_entities(QUERY_B) == [
            "Shopify", "WooCommerce", "BigCommerce",
        ]

    def test_case_c_sentence_words_yield_no_entities(self):
        entities = detect_entities(QUERY_C)
        assert entities == []
        for ghost in ("My", "If", "SaaS", "Projected Results Visually"):
            assert ghost not in entities

    def test_case_d_camelcase_detected_whole_no_ai_fragment(self):
        entities = detect_entities(QUERY_D)
        assert "OpenAI" in entities
        assert "Anthropic" in entities
        assert "AI" not in entities
        # One entity per mention: no fragment pair for "Meta AI".
        assert not ({"META", "Meta"} <= set(entities))

    def test_case_e_imperative_verbs_yield_no_entities(self):
        from app.services.llm.langchain_pipeline import (
            _historical_comparison_gate,
        )

        assert detect_entities(QUERY_E) == []
        for ghost in ("Model", "Keep", "Rank", "Show"):
            assert ghost not in detect_entities(QUERY_E)
        assert _fallback_entities_from_text(QUERY_E) == []
        gate = _historical_comparison_gate(QUERY_E)
        assert gate["applies"] is False


class TestGateCascade:
    """With zero valid entities the comparison gate must not apply."""

    def test_case_a_gate_cannot_apply(self):
        decomposed = decompose_comparison_query(QUERY_A)
        assert len(decomposed["entities"]) < 2

    def test_case_c_gate_cannot_apply(self):
        decomposed = decompose_comparison_query(QUERY_C)
        assert len(decomposed["entities"]) < 2


class TestFallbackInlet:
    """The web_search deterministic fallback must apply the same floor."""
    def test_fallback_case_a(self):
        assert _fallback_entities_from_text(QUERY_A) == []

    def test_fallback_case_c(self):
        entities = _fallback_entities_from_text(QUERY_C)
        assert "My" not in entities
        assert "If" not in entities


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in for httpx.AsyncClient serving canned Yahoo search quotes."""

    def __init__(self, quotes):
        self._quotes = quotes

    async def get(self, *args, **kwargs):
        return _FakeResp({"quotes": self._quotes})


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestPrivateCompanies:
    """Case D must never produce a ticker for OpenAI or Anthropic."""

    def test_known_privates_typed_private(self):
        for name in ("OpenAI", "Anthropic", "SpaceX", "Stripe", "ByteDance"):
            assert classify_entity_type(name) == "PRIVATE_COMPANY"
            assert may_attempt_market_resolution(name) is False

    def test_public_companies_unaffected(self):
        assert classify_entity_type("Tesla") == "PUBLIC_COMPANY"
        assert classify_entity_type("Google") == "PUBLIC_COMPANY"
        assert may_attempt_market_resolution("Tesla") is True

    def test_resolver_short_circuits_privates_without_network(self):
        quotes = [
            {
                "symbol": "AI",
                "longname": "C3.ai, Inc.",
                "quoteType": "EQUITY",
            }
        ]
        assert _run(_resolve_symbol(_FakeClient(quotes), "OpenAI")) is None
        assert _run(_resolve_symbol(_FakeClient(quotes), "Anthropic")) is None


class TestSymbolNameValidation:
    """First Yahoo hit wins is over: unrelated names must be rejected."""
    def test_rejects_if_infineon(self):
        quote = {
            "symbol": "IFX.DE",
            "longname": "Infineon Technologies AG",
            "quoteType": "EQUITY",
        }
        assert _quote_matches_entity("If", quote) is False
        assert _run(_resolve_symbol(_FakeClient([quote]), "If")) is None

    def test_rejects_openai_c3ai(self):
        quote = {
            "symbol": "AI",
            "longname": "C3.ai, Inc.",
            "quoteType": "EQUITY",
        }
        assert _quote_matches_entity("OpenAI", quote) is False

    def test_rejects_thematic_etf_without_fund_ask(self):
        # Live Yahoo first-hit shape for "Anthropic": a third-party
        # thematic ETF sharing one name word. Token overlap alone would
        # accept it; the fund-vehicle guard must reject it.
        quote = {
            "symbol": "ANTW",
            "longname": "Anthropic AI Lab Ecosystem ETF",
            "quoteType": "ETF",
        }
        assert _quote_matches_entity("Anthropic Labs", quote) is False

    def test_accepts_explicit_etf_ask(self):
        assert _quote_matches_entity(
            "VTI",
            {"symbol": "VTI", "longname": "Vanguard Total Stock Market ETF",
             "quoteType": "ETF"},
        ) is True

    def test_accepts_genuine_matches(self):
        assert _quote_matches_entity(
            "Tesla", {"symbol": "TSLA", "longname": "Tesla, Inc."}
        ) is True
        assert _quote_matches_entity(
            "Shopify", {"symbol": "SHOP", "longname": "Shopify Inc."}
        ) is True
        assert _quote_matches_entity(
            "Samsung",
            {"symbol": "005930.KS", "longname": "Samsung Electronics Co., Ltd."},
        ) is True
        assert _run(
            _resolve_symbol(
                _FakeClient(
                    [
                        {
                            "symbol": "SHOP",
                            "longname": "Shopify Inc.",
                            "quoteType": "EQUITY",
                        }
                    ]
                ),
                "Shopify",
            )
        ) == "SHOP"


def _price(ent):
    return {
        "entity": ent, "symbol": "X", "currency": "USD",
        "labels": ["2022-01-01", "2023-01-01", "2024-01-01", "2025-01-01"],
        "values": [100.0, 110.0, 120.0, 130.0],
        "range": "3y", "frequency": "weekly",
        "period_start": "2022-01-01", "period_end": "2025-01-01",
        "is_historical": True, "metric": "close",
    }


def _fin(ent):
    def blk(values, metric):
        return {
            "labels": ["2022", "2023", "2024"], "values": values,
            "period_start": "2022", "period_end": "2024",
            "metric": metric, "definition": metric,
            "unit": "USD", "currency": "USD",
            "frequency": "annual", "source_type": "yahoo",
        }

    return {
        "entity": ent, "symbol": "X", "currency": "USD",
        "frequency": "annual", "is_historical": True,
        "revenue": blk([100.0, 110.0, 120.0], "annualTotalRevenue"),
        "net_income": blk([50.0, 55.0, 60.0], "annualNetIncome"),
    }


_PARTIAL_QUERY = (
    "Compare Tesla, BYD, and Toyota over the last 3 years. Which company "
    "had the highest revenue growth, strongest stock price performance, "
    "and best profitability?"
)


class TestNoRawReprLeak:
    """No user-facing exclusion text may contain raw Python list repr."""

    def test_partial_gate_note_is_plain_language(self):
        from app.services.llm.langchain_pipeline import (
            _historical_comparison_gate,
        )

        gate = _historical_comparison_gate(
            _PARTIAL_QUERY,
            price_history=[_price("Tesla"), _price("BYD")],
            financial_history=[_fin("Tesla"), _fin("BYD")],
        )
        assert gate["partial"] is True
        note = gate["exclusion_note"]
        assert "['" not in note
        assert '"]' not in note
        assert "Toyota" in note

    def test_partial_metric_gap_names_entity_and_metric(self):
        """Stock validated for all, financials for a subset: the note must
        name the entity AND the metrics it lacks (never silent, never repr)."""
        from app.services.llm.langchain_pipeline import (
            _historical_comparison_gate,
        )

        gate = _historical_comparison_gate(
            _PARTIAL_QUERY,
            price_history=[
                _price("Tesla"), _price("BYD"), _price("Toyota"),
            ],
            financial_history=[_fin("Tesla"), _fin("BYD")],
        )
        assert gate["partial"] is True
        note = gate["exclusion_note"]
        assert "['" not in note
        assert "Toyota" in note
        assert "revenue_growth" in note or "profitability" in note

    def test_completeness_note_is_plain_language(self):
        from app.services.data.comparison import (
            build_research_plan,
            check_research_completeness,
        )

        plan = build_research_plan(_PARTIAL_QUERY)
        out = check_research_completeness(
            plan,
            {
                "price_history": [], "financial_history": [],
                "market_data": [], "fundamentals": [], "snippets": [],
            },
        )
        assert "['" not in out["exclusion_note"]
        assert "Toyota" in out["exclusion_note"]

    def test_renderer_survives_quote_in_name(self):
        from app.services.data.canonical import exclusion_note_for

        note = exclusion_note_for(
            {
                "excluded_entities": [{"entity": "O'Brien"}],
                "excluded_metrics": [],
            }
        )
        assert "O'Brien" in note
        assert "['" not in note


class TestGateScopeEarlyOut:
    """The market-evidence gate must not apply when no requested entity
    can enter market adapters (all geographies, all private companies)."""

    def test_all_geography_skips_gate(self):
        from app.services.llm.langchain_pipeline import (
            _historical_comparison_gate,
        )

        gate = _historical_comparison_gate(
            "Compare America and Britain over the last 5 years on "
            "GDP growth and inflation."
        )
        assert gate["entities"] == ["America", "Britain"]
        assert gate["applies"] is False

    def test_all_private_skips_gate(self):
        from app.services.llm.langchain_pipeline import (
            _historical_comparison_gate,
        )

        gate = _historical_comparison_gate(
            "Compare OpenAI and Anthropic over the last 3 years on "
            "funding and valuation."
        )
        assert gate["applies"] is False

    def test_public_companies_still_gated(self):
        from app.services.llm.langchain_pipeline import (
            _historical_comparison_gate,
        )

        gate = _historical_comparison_gate(
            "Compare Tesla and Toyota over the last 3 years on revenue "
            "growth and stock performance."
        )
        assert gate["applies"] is True


class TestHonestMacroGap:
    """Case A must disclose the structural macro gap instead of implying
    no evidence exists anywhere."""

    def test_gap_note_names_key_mapping_and_us_only(self):
        from types import SimpleNamespace

        from app.services.web_search import _macro_gap_note

        note = _macro_gap_note(
            QUERY_A,
            [("US Consumer Price Index", "CPIAUCSL")],
            [],
            SimpleNamespace(FRED_API_KEY=None),
        )
        assert "no FRED_API_KEY" in note
        assert "mortgage rates" in note
        assert "US-only" in note
        assert "['" not in note

    def test_no_note_when_macro_unwanted_or_present(self):
        from types import SimpleNamespace

        from app.services.web_search import _macro_gap_note

        settings = SimpleNamespace(FRED_API_KEY=None)
        assert (
            _macro_gap_note(
                "Compare Tesla and Toyota on revenue", [], [], settings
            )
            == ""
        )
        assert (
            _macro_gap_note(
                QUERY_A,
                [("US Consumer Price Index", "CPIAUCSL")],
                [{"entity": "US Consumer Price Index"}],
                settings,
            )
            == ""
        )

    def test_prompt_carries_gap_notice(self):
        from app.services.llm.langchain_pipeline import build_prompt

        prompt = build_prompt(
            "q", [], {}, [], "live_web",
            macro_note="Structured macro coverage is US-only (FRED).",
        )
        assert "Data-gap notice" in prompt
        assert "US-only" in prompt
        plain = build_prompt("q", [], {}, [], "live_web")
        assert "Data-gap notice" not in plain


class TestUnionInlet:
    """LLM-framed entities pass the same floor (no raw inlet)."""

    def test_hostile_framed_entities_dropped_or_cleaned(self):
        from app.services.web_search import _union_entities

        merged = _union_entities(
            ["My", "If", "AI", "And BigCommerce"],
            "My SaaS has customers. If I raise price, and BigCommerce...",
        )
        assert "My" not in merged
        assert "If" not in merged
        assert "AI" not in merged
        assert "And BigCommerce" not in merged

    def test_case_d_union_has_no_fragments(self):
        from app.services.web_search import _union_entities

        merged = _union_entities(
            ["OpenAI", "Anthropic", "Google", "Meta"],
            QUERY_D,
        )
        assert "AI" not in merged
        # No doubled mention: Google subsumed by Google DeepMind.
        assert not ({"Google", "Google DeepMind"} <= set(merged))

    def test_union_still_recovers_dropped_company(self):
        from app.services.web_search import _union_entities

        assert _union_entities(
            ["Tesla", "BYD"],
            "Compare Tesla, BYD, and Toyota over the last 3 years.",
        ) == ["Tesla", "BYD", "Toyota"]
