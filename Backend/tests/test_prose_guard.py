"""Post-narration prose guards: number grounding, citation support, rescue path,
fail-closed behaviour, and the end-to-end wiring through run_pipeline."""

import asyncio
import json
from types import SimpleNamespace

import app.services.llm.langchain_pipeline as pipeline_mod
from app.services.data.stats import compute_statistics
from app.services.llm.langchain_pipeline import run_pipeline
from app.services.llm.pipeline import guard_metrics, prose_guard
from app.services.llm.pipeline.models import ClarificationRequest, PipelineOutput
from app.services.llm.pipeline.prompting import fallback_output

ROWS = [{"region": "East", "revenue": 500}, {"region": "West", "revenue": 470}]
SNIPPETS = ["Acme raised $50M in a Series B round led by Foo Capital in 2024."]


def _output(answer, confidence=0.9):
    return PipelineOutput(
        answer=answer, visuals=[], insights=[], summary="", root_causes=[],
        recommendations=[], news_context=[], anomalies=[], confidence=confidence,
    )


def _modes(monkeypatch, number="flag", citation="flag", rescue=True):
    monkeypatch.setattr(
        prose_guard,
        "get_settings",
        lambda: SimpleNamespace(
            PROSE_NUMBER_GUARD_MODE=number,
            CITATION_SUPPORT_GUARD_MODE=citation,
            PROSE_RESCUE_STRICT=rescue,
            FAIL_CLOSED_GUARDS=True,
        ),
    )


def _run(output, snippets=None, rows=ROWS, computed=None):
    thinking = []
    guard_metrics.begin_request()
    result = prose_guard.enforce_prose_guards(
        output,
        query="revenue by region",
        plan_query="revenue by region",
        snippets=snippets or [],
        rows=rows,
        price_history=[],
        financial_history=[],
        market_data=[],
        computed_numbers=computed or {},
        thinking=thinking,
    )
    return result, thinking


# --- number guard ---------------------------------------------------------

def test_grounded_numbers_are_left_alone(monkeypatch):
    _modes(monkeypatch)
    result, thinking = _run(_output("East revenue was 500 and West was 470."))
    assert result.confidence == 0.9
    assert thinking == []
    assert guard_metrics.request_events() == []


def test_untraceable_number_is_flagged_and_confidence_capped(monkeypatch):
    _modes(monkeypatch)
    result, thinking = _run(_output("East revenue was 500. West revenue was 7777."))
    assert result.confidence == prose_guard.CONFIDENCE_CAP
    assert "7777" in result.answer  # flag mode never edits the prose
    assert any("could not be traced" in note for note in thinking)
    assert any(
        event.startswith("prose_number_untraceable")
        for event in guard_metrics.request_events()
    )


def test_strip_mode_removes_only_the_offending_sentence(monkeypatch):
    _modes(monkeypatch, number="strip")
    result, _ = _run(_output("East revenue was 500. West revenue was 7777."))
    assert "7777" not in result.answer
    assert "East revenue was 500." in result.answer
    assert "could not be verified" in result.answer


def test_strip_mode_with_nothing_verifiable_says_so(monkeypatch):
    _modes(monkeypatch, number="strip")
    result, _ = _run(_output("West revenue was 7777."))
    assert result.answer == prose_guard._NOTHING_VERIFIED


def test_counts_dates_and_labels_are_not_claims(monkeypatch):
    _modes(monkeypatch)
    result, thinking = _run(
        _output("There are 3 regions. In Q1 2025 (2025-03-01 to 2025-03-12) East led.")
    )
    assert thinking == []
    assert result.confidence == 0.9


def test_small_percentages_are_still_claims(monkeypatch):
    _modes(monkeypatch)
    _, thinking = _run(_output("Revenue grew 5%."))
    assert any("could not be traced" in note for note in thinking)


def test_derived_numbers_are_grounded(monkeypatch):
    _modes(monkeypatch)
    # 970 = 500 + 470 ; 6.4% = (500 - 470) / 470
    result, thinking = _run(
        _output("Together they brought in 970. East is 6.4% ahead of West.")
    )
    assert thinking == []
    assert result.confidence == 0.9


def test_number_from_the_users_own_question_is_grounded(monkeypatch):
    _modes(monkeypatch)
    thinking = []
    guard_metrics.begin_request()
    result = prose_guard.enforce_prose_guards(
        _output("If revenue were 8000, East would still lead."),
        query="what if revenue were 8000", plan_query="what if revenue were 8000",
        snippets=[], rows=ROWS, price_history=[], financial_history=[],
        market_data=[], computed_numbers={}, thinking=thinking,
    )
    assert thinking == []
    assert result.confidence == 0.9


def test_mode_off_does_nothing(monkeypatch):
    _modes(monkeypatch, number="off", citation="off")
    result, thinking = _run(_output("West revenue was 7777."))
    assert result.confidence == 0.9
    assert thinking == []


def test_clarifications_make_no_numeric_claims(monkeypatch):
    _modes(monkeypatch, number="strip")
    output = _output("")
    output.clarification = ClarificationRequest(question="Which month, 9999?")
    result, thinking = _run(output)
    assert result.clarification is not None
    assert thinking == []


def test_untraceable_number_in_insight_is_dropped_in_strip_mode(monkeypatch):
    _modes(monkeypatch, number="strip")
    output = _output("East revenue was 500.")
    output.insights = ["East led with 500.", "Churn is 4567."]
    result, _ = _run(output)
    assert result.insights == ["East led with 500."]


# --- citation support -----------------------------------------------------

def test_citation_to_an_unrelated_snippet_is_flagged(monkeypatch):
    _modes(monkeypatch)
    result, thinking = _run(
        _output("Globex opened new offices across Europe and Asia this quarter [1]."),
        snippets=SNIPPETS, rows=[],
    )
    assert any("not supported by their source" in note for note in thinking)
    assert result.confidence == prose_guard.CONFIDENCE_CAP
    assert any(
        event.startswith("citation_unsupported")
        for event in guard_metrics.request_events()
    )


def test_supported_citation_passes(monkeypatch):
    _modes(monkeypatch)
    result, thinking = _run(
        _output("Acme raised $50M in a Series B round [1]."), snippets=SNIPPETS, rows=[]
    )
    assert thinking == []
    assert result.confidence == 0.9


def test_citation_strip_mode_removes_the_unsupported_sentence(monkeypatch):
    _modes(monkeypatch, citation="strip")
    result, _ = _run(
        _output(
            "East revenue was 500. "
            "Globex opened new offices across Europe and Asia this quarter [1]."
        ),
        snippets=SNIPPETS,
    )
    assert "Globex" not in result.answer
    assert "East revenue was 500." in result.answer


# --- rescue path + fail-closed ----------------------------------------------

def test_rescue_guard_strips_untraceable_sentences(monkeypatch):
    _modes(monkeypatch)
    rescued = fallback_output(reason="East revenue was 500. Growth was 7777.", confidence=0.35)
    result = prose_guard.guard_rescued_prose(
        rescued, query="q", snippets=[], rows=ROWS, market_data=[], computed_numbers={}
    )
    assert result.answer == "East revenue was 500."


def test_rescue_guard_falls_back_when_nothing_is_verifiable(monkeypatch):
    _modes(monkeypatch)
    rescued = fallback_output(reason="Growth was 7777.", confidence=0.35)
    result = prose_guard.guard_rescued_prose(
        rescued, query="q", snippets=[], rows=ROWS, market_data=[], computed_numbers={}
    )
    assert result.confidence == 0.0
    assert result.answer == prose_guard._NOTHING_VERIFIED


def test_rescue_guard_can_be_disabled(monkeypatch):
    _modes(monkeypatch, rescue=False)
    rescued = fallback_output(reason="Growth was 7777.", confidence=0.35)
    result = prose_guard.guard_rescued_prose(
        rescued, query="q", snippets=[], rows=ROWS, market_data=[], computed_numbers={}
    )
    assert result.answer == "Growth was 7777."


def test_guard_crash_fails_closed(monkeypatch):
    _modes(monkeypatch)
    output = _output("Anything.", confidence=0.9)
    thinking = []
    guard_metrics.begin_request()
    prose_guard.fail_closed_after_guard_error(
        output, thinking, "narration_contract", RuntimeError("boom")
    )
    assert output.confidence == prose_guard.CONFIDENCE_CAP
    assert any("unavailable" in note for note in thinking)
    assert any(
        event.startswith("guard_error:narration_contract")
        for event in guard_metrics.request_events()
    )


# --- end to end through run_pipeline ------------------------------------------

def _sequenced_fake(*contents):
    calls = {"n": 0}

    async def fake(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
        content = contents[min(calls["n"], len(contents) - 1)]
        calls["n"] += 1
        return {"content": json.dumps(content), "source": "groq", "usage": None}

    return fake


def _decision_json():
    return {
        "decision": "answer", "missing": "", "chart_from_prior": False,
        "visual_plan": [], "suggested_options": [],
    }


def _pipeline_json(answer, confidence):
    return {
        "answer": answer, "visuals": [], "insights": [], "summary": "",
        "root_causes": [], "recommendations": [], "news_context": [],
        "anomalies": [], "confidence": confidence, "clarification": None,
    }


def test_pipeline_flags_an_invented_number_end_to_end(monkeypatch):
    monkeypatch.setattr(
        pipeline_mod,
        "generate_response",
        _sequenced_fake(
            _decision_json(),
            _pipeline_json("East revenue was 7777.", 0.9),
        ),
    )
    output = asyncio.run(
        run_pipeline(
            user_query="revenue by region",
            db_data=ROWS,
            computed_numbers=compute_statistics(ROWS),
            news_context=[],
            source_scope="own_data",
        )
    )
    assert output.confidence <= prose_guard.CONFIDENCE_CAP
    guards = (output.research_state or {}).get("guards") or []
    assert any(g.startswith("prose_number_untraceable") for g in guards)
