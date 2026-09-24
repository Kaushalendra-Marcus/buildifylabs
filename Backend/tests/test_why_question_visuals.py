"""Regression tests for junk visuals on WHY questions (live-web scope).

Reproduces the Tesla screenshots: "why sales of tesala cars is dropping?"
produced a "Cited percents compared" bar (a 9% Europe figure vs a 7%
global-forecast figure from different sources), a "Revenue compared" table
built from the same unrelated pair, and the internal
"Excluded from this comparison (insufficient validated evidence)" sentence
pasted into the answer prose.

A why-question must get prose + citable figures, never a comparison of
unrelated numbers and never pipeline jargon.
"""

from app.services.data.comparison.figures import figures_share_metric
from app.services.data.canonical import exclusion_note_for
from app.services.llm.pipeline.figures import (
    _figures_from_snippets,
    _resolve_chart_entity,
)
from app.services.llm.pipeline.run import _disclose_exclusion_for_query
from app.services.llm.pipeline.visuals import (
    _clean_figure_context,
    _figures_bar_visual,
    _product_table_visual,
)

QUERY = "why sales of tesala cars is dropping?"

SNIPPETS = [
    "Tesla's European sales fell 9 percent in October as competition "
    "intensified, Ars Technica reports.",
    "Tesla's global vehicle deliveries are expected to decline 7 percent "
    "this year, according to Visible Alpha, after a 1 percent drop in 2024.",
]


def tesla_figures():
    return _figures_from_snippets(SNIPPETS, QUERY)


class TestWhyQuestionFigures:
    def test_unrelated_percents_produce_no_bar(self):
        assert _figures_bar_visual(tesla_figures(), QUERY) is None

    def test_unrelated_percents_produce_no_product_table(self):
        assert _product_table_visual(tesla_figures(), QUERY) is None

    def test_source_names_are_not_chart_entities(self):
        figures = tesla_figures()
        assert figures, "expected figures extracted from the snippets"
        for figure in figures:
            entity = _resolve_chart_entity(figure, [])
            assert entity not in ("Ars Technica", "Visible Alpha"), (
                f"source name resolved as chart entity: {entity}"
            )


class TestMetricAgreement:
    def test_same_cue_is_comparable(self):
        figs = [
            {"context": "Tesla sales rose 9 percent"},
            {"context": "Tesla sales fell 7 percent"},
        ]
        assert figures_share_metric(figs)[0] is True

    def test_all_uncued_stays_comparable(self):
        figs = [{"context": "unemployment at 5 percent"}, {"context": "at 3 percent"}]
        assert figures_share_metric(figs)[0] is True

    def test_explicitly_different_cues_are_not_comparable(self):
        figs = [
            {"context": "Acme raised $300k in funding"},
            {"context": "startup cost was $40k"},
        ]
        assert figures_share_metric(figs)[0] is False

    def test_mixed_cued_and_uncued_percent_is_not_comparable(self):
        # The Tesla case: one percent cued "revenue" (sales), the other with
        # no metric cue at all (deliveries decline). A percent without a WHAT
        # has no comparable dimension, so charting them together compares
        # unrelated meanings.
        figs = [
            {"unit": "percent", "context": "Tesla European sales fell 9 percent"},
            {"unit": "percent", "context": "deliveries expected to decline 7 percent"},
        ]
        assert figures_share_metric(figs)[0] is False

    def test_mixed_cued_and_uncued_money_stays_comparable(self):
        # Legacy leniency: amounts of money share an absolute scale (the
        # currency gate handles known-different money separately).
        figs = [
            {"unit": "money", "context": "HeadshotPro earned $300k per month"},
            {"unit": "money", "context": "Headlime was sold for $1M"},
        ]
        assert figures_share_metric(figs)[0] is True


class TestExclusionDisclosure:
    def test_why_question_suppresses_exclusion_note(self):
        assert _disclose_exclusion_for_query(QUERY) is False
        assert (
            _disclose_exclusion_for_query(
                "Would you like a regional breakdown of the sales decline?"
            )
            is False
        )

    def test_comparison_query_keeps_exclusion_note(self):
        assert _disclose_exclusion_for_query("tesla vs byd sales") is True

    def test_exclusion_names_render_as_plain_words(self):
        state = {
            "excluded_entities": [],
            "excluded_metrics": [{"metric": "revenue_growth"}],
        }
        note = exclusion_note_for(state)
        assert "revenue growth" in note
        assert "revenue_growth" not in note

    def test_no_exclusions_no_note(self):
        assert exclusion_note_for({"excluded_entities": [], "excluded_metrics": []}) == ""


class TestFigureContextCleanup:
    def test_leading_ellipsis_stripped(self):
        assert _clean_figure_context("...much on Europe as on China...") == (
            "much on Europe as on China..."
        )

    def test_clean_context_untouched(self):
        assert _clean_figure_context("Tesla sales fell 9 percent") == (
            "Tesla sales fell 9 percent"
        )
