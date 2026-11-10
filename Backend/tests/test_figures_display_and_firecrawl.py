"""Figures-cited table (crore/lakh scale, one-fragment readable context) and the
Firecrawl cost guards (per-request cap, daily budget, scrape toggle)."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import web_search as ws
from app.services.llm.pipeline.figures import _figures_from_snippets
from app.services.llm.pipeline.grounding import _parse_scaled_number
from app.services.llm.pipeline.visuals import _clean_figure_context, _figures_table_visual

SNIPPET = (
    "Namit Malhotra Net Worth 2026: The Rise of a Media Mogul's ...: it became a "
    "\u20b9100 crore/year revenue generator by 2024, with projections suggesting it "
    "will cross \u20b9200 crore by 2026. 200+ crore/year revenue ..."
)


# --- crore / lakh ------------------------------------------------------------------

def test_crore_stays_in_the_figure_text_and_is_scaled():
    figures = {f["text"]: f for f in _figures_from_snippets([SNIPPET])}
    assert "\u20b9100 crore" in figures  # was captured as just "\u20b9100"
    assert "\u20b9200 crore" in figures
    assert figures["\u20b9100 crore"]["value"] == 1e9
    assert figures["\u20b9200 crore"]["value"] == 2e9


def test_grounding_parser_understands_indian_units():
    assert _parse_scaled_number("\u20b9100 crore/year") == [1e9]
    assert _parse_scaled_number("about 5 lakh users") == [500000.0]
    assert _parse_scaled_number("\u20b95 Cr.") == [5e7]
    # Unchanged behaviour for the existing scale words.
    assert _parse_scaled_number("$409.3 million") == [409.3e6]


# --- readable display excerpt -----------------------------------------------------------

def test_display_context_comes_from_one_fragment_without_dots():
    figure = {f["text"]: f for f in _figures_from_snippets([SNIPPET])}["\u20b9100 crore"]
    excerpt = figure["display_context"]
    assert "..." not in excerpt and "\u2026" not in excerpt
    assert excerpt.startswith("it became a \u20b9100 crore/year revenue generator")
    assert excerpt.endswith("by 2026.")
    assert "Namit Malhotra" not in excerpt  # the title fragment is not glued on


def test_binding_context_is_unchanged_by_the_display_excerpt():
    # `context` still feeds entity binding / chart labels; only the table uses
    # the display excerpt.
    figure = _figures_from_snippets([SNIPPET])[0]
    assert "context" in figure and "display_context" in figure


def test_figures_table_rows_have_no_dot_joins():
    figures = _figures_from_snippets([SNIPPET])
    table = _figures_table_visual(figures)
    rows = table.props["values"]
    assert rows[0][0] == "\u20b9100 crore [1]"
    assert all("..." not in row[1] for row in rows)


def test_clean_figure_context_handles_joins():
    assert _clean_figure_context("Media Mogul's ...: it became") == "Media Mogul's: it became"
    assert _clean_figure_context("Title ... body") == "Title \u2013 body"
    assert _clean_figure_context("...much on Europe") == "much on Europe"


# --- Firecrawl cost guards ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_firecrawl_state():
    token = ws._firecrawl_request_state.set(None)
    yield
    ws._firecrawl_request_state.reset(token)


def _settings(**overrides):
    base = dict(
        FIRECRAWL_API_KEY="k",
        WEB_SEARCH_MAX_RESULTS=5,
        FIRECRAWL_MAX_RESULTS=3,
        FIRECRAWL_MAX_QUERIES_PER_REQUEST=2,
        FIRECRAWL_DAILY_CREDIT_BUDGET=0,
        FIRECRAWL_SCRAPE_RESULTS=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_per_request_cap_blocks_the_third_search():
    ws._firecrawl_request_state.set({"calls": 0})
    settings = _settings()
    assert [ws._firecrawl_allowed(settings) for _ in range(4)] == [True, True, False, False]


def test_cap_is_shared_across_gathered_tasks():
    settings = _settings()

    async def scenario():
        ws._firecrawl_request_state.set({"calls": 0})

        async def one():
            return ws._firecrawl_allowed(settings)

        return await asyncio.gather(*(one() for _ in range(5)))

    assert asyncio.run(scenario()).count(True) == 2


def test_no_cap_outside_a_request_or_when_zero():
    ws._firecrawl_request_state.set(None)
    assert all(ws._firecrawl_allowed(_settings()) for _ in range(5))
    ws._firecrawl_request_state.set({"calls": 0})
    unlimited = _settings(FIRECRAWL_MAX_QUERIES_PER_REQUEST=0)
    assert all(ws._firecrawl_allowed(unlimited) for _ in range(5))


def test_daily_budget_blocks_once_estimated_credits_are_spent(monkeypatch):
    monkeypatch.setitem(ws._firecrawl_daily, "day", ws._firecrawl_today())
    monkeypatch.setitem(ws._firecrawl_daily, "credits", 0)
    settings = _settings(FIRECRAWL_DAILY_CREDIT_BUDGET=10)
    assert ws._firecrawl_allowed(settings) is True
    assert ws._firecrawl_record_credits(3, scrape=True) == 5  # 2 search + 3 scraped
    assert ws._firecrawl_record_credits(3, scrape=True) == 5
    assert ws._firecrawl_allowed(settings) is False
    assert ws._firecrawl_record_credits(3, scrape=False) == 2  # search only


def test_firecrawl_search_honours_cap_and_scrape_toggle(monkeypatch):
    monkeypatch.setitem(ws._firecrawl_daily, "day", ws._firecrawl_today())
    monkeypatch.setitem(ws._firecrawl_daily, "credits", 0)
    calls = []

    class FakeClient:
        async def search(self, query, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                web=[SimpleNamespace(
                    title="T", url="https://x.test", description="desc text", position=1
                )],
                news=None,
                warning=None,
            )

    monkeypatch.setattr(ws, "_get_firecrawl_client", lambda settings: FakeClient())
    settings = _settings(FIRECRAWL_MAX_QUERIES_PER_REQUEST=1, FIRECRAWL_SCRAPE_RESULTS=False)

    async def scenario():
        ws._firecrawl_request_state.set({"calls": 0})
        first = await ws._firecrawl_search(None, "q1", settings)
        second = await ws._firecrawl_search(None, "q2", settings)
        return first, second

    first, second = asyncio.run(scenario())
    assert len(first) == 1
    assert second == []  # capped: no second provider call
    assert len(calls) == 1
    assert "scrape_options" not in calls[0]  # search-only: 2 credits, not 2 + n
