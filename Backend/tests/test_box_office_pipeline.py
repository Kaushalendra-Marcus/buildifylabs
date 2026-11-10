from app.services.box_office import extract_box_office_history
from app.services.llm.pipeline.guarantee import ensure_visuals
from app.services.llm.pipeline.models import PipelineOutput


SNIPPETS = [
    "The film collected Rs 35 crore on day 1 and Rs 42.5 crore on day 2.",
    "It was made on a budget of Rs 200 crore and released on 12 June 2026.",
    "Worldwide gross reached Rs 375.50 crore within its opening weekend, up 45.3 percent.",
]


def _output():
    return PipelineOutput(
        answer="Daily collection is available.",
        visuals=[],
        insights=[],
        summary="",
        root_causes=[],
        recommendations=[],
        news_context=[],
        anomalies=[],
        confidence=0.8,
    )


def test_extracts_only_explicit_release_days_and_budget():
    evidence = extract_box_office_history(SNIPPETS, [{}, {}, {}])
    assert [point["day"] for point in evidence["points"]] == [1, 2]
    assert evidence["points"][0]["value"] == 35e7
    assert evidence["budget"]["value"] == 200e7
    assert evidence["release_date"] == "12 June 2026"


def test_box_office_evidence_builds_daily_graph_table_and_coverage():
    evidence = extract_box_office_history(SNIPPETS, [{}, {}, {}])
    result = ensure_visuals(
        _output(),
        rows=[],
        computed_numbers={},
        market_data=[],
        web_sources=[
            {"title": "daily", "url": "https://example.com/1", "provider": "Tavily"},
            {"title": "budget", "url": "https://example.com/2", "provider": "Tavily"},
            {"title": "total", "url": "https://example.com/3", "provider": "Tavily"},
        ],
        news_context=SNIPPETS,
        preferred_visual=None,
        query="total collection and daily box office",
        box_office_history=evidence,
    )
    titles = {visual.title for visual in result.visuals}
    assert "Daily box-office collection" in titles
    assert "Daily collection details" in titles
    assert "Reported collection vs budget" in titles
    graph = next(v for v in result.visuals if v.title == "Daily box-office collection")
    assert graph.props["labels"] == ["Day 1", "Day 2"]
    assert graph.props["datasets"][0]["values"] == [35e7, 42.5e7]
    assert all(v.title != "Cited percents compared" for v in result.visuals)
