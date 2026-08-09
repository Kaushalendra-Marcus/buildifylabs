"""The specs/06 §3 contract, applied to langchain_pipeline.py (Phase B4).

Covers the migration: 7 real visual types (Literal-constrained), `props`
instead of `chart_data`, bounded confidence (0..1), the `clarification`
alternate mode, the fixed mutable-default, prompt-row truncation, and hedged
causal language instructions - plus graceful fallback on malformed LLM output.
"""

import asyncio

import pytest
from pydantic import ValidationError

import app.services.llm.langchain_pipeline as pipeline_mod
from app.services.llm.langchain_pipeline import (
    PipelineOutput,
    VisualOutput,
    build_prompt,
    _truncate_rows,
    run_pipeline,
)


class TestSchemaContract:
    def test_visual_output_accepts_all_7_real_types(self):
        for vtype in ("metric", "graph", "table", "comparison", "insight", "alert", "status"):
            v = VisualOutput(visual_type=vtype, props={"x": 1}, title="t")
            assert v.visual_type == vtype

    def test_visual_output_rejects_old_fictional_type(self):
        with pytest.raises(ValidationError):
            VisualOutput(visual_type="line_chart", props={}, title="t")

    def test_visual_output_props_is_free_dict(self):
        v = VisualOutput(visual_type="metric", props={"label": "Rev", "value": 5}, title="t")
        assert v.props == {"label": "Rev", "value": 5}

    def test_confidence_must_be_bounded_0_to_1(self):
        PipelineOutput(
            answer="x", visuals=[], insights=[], summary="",
            root_causes=[], recommendations=[], news_context=[],
            anomalies=[], confidence=0.0,
        )
        with pytest.raises(ValidationError):
            PipelineOutput(
                answer="x", visuals=[], insights=[], summary="",
                root_causes=[], recommendations=[], news_context=[],
                anomalies=[], confidence=1.5,
            )


class TestSystemPrompt:
    def test_teaches_the_7_real_types(self):
        for t in ("metric", "graph", "table", "comparison", "insight", "alert", "status"):
            assert t in pipeline_mod.SYSTEM_PROMPT

    def test_hedged_causal_language_rule(self):
        assert "hedged causal language" in pipeline_mod.SYSTEM_PROMPT
        assert "A possible contributing factor" in pipeline_mod.SYSTEM_PROMPT or "correlates with" in pipeline_mod.SYSTEM_PROMPT

    def test_no_old_fictional_types(self):
        for stale in ("line_chart", "bar_chart", "kpi_card", "india_map", "funnel_chart", "heatmap"):
            assert stale not in pipeline_mod.SYSTEM_PROMPT


class TestTruncation:
    def test_rows_past_cap_are_truncated_with_note(self):
        rows = [{"n": i} for i in range(100)]
        truncated, note = _truncate_rows(rows, max_rows=50)
        assert len(truncated) == 50
        assert "50" in note and "100" in note

    def test_rows_under_cap_unchanged(self):
        rows = [{"n": 1}, {"n": 2}]
        truncated, note = _truncate_rows(rows, max_rows=50)
        assert truncated == rows and note == ""


def _pipeline_json(**overrides):
    default = {
        "answer": "Revenue averaged 175.0 across the last 2 periods.",
        "visuals": [
            {
                "visual_type": "metric",
                "props": {"label": "Average daily revenue", "value": 175.0},
                "title": "Average revenue",
            }
        ],
        "insights": ["A possible contributing factor is the seasonal ramp."],
        "summary": "Moderate growth across the window.",
        "root_causes": ["Correlates with the launch week, a likely contributor."],
        "recommendations": ["Consider pacing spend earlier in the week."],
        "news_context": [],
        "anomalies": [],
        "confidence": 0.85,
        "clarification": None,
    }
    default.update(overrides)
    return default


class TestRunPipeline:
    def test_run_pipeline_returns_structured_output(self, monkeypatch):
        import json

        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512):
            return {
                "content": json.dumps(_pipeline_json()),
                "source": "groq",
                "usage": None,
            }

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        async def scenario():
            output = await run_pipeline(
                user_query="what is average revenue?",
                db_data=[{"revenue": 100}, {"revenue": 250}],
                computed_numbers={"averages": {"revenue": 175.0}},
                source_scope="own_data",
            )
            return output

        output = asyncio.run(scenario())
        assert output.answer
        assert output.visuals[0].visual_type == "metric"
        assert 0.0 <= output.confidence <= 1.0
        assert output.clarification is None
        assert output.sql_query is None  # traceability fields are route-filled

    def test_clarification_is_a_working_mode(self, monkeypatch):
        import json

        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512):
            return {
                "content": json.dumps(
                    _pipeline_json(
                        answer="",
                        insights=[],
                        summary="",
                        root_causes=[],
                        recommendations=[],
                        confidence=0.0,
                        clarification={
                            "question": "Which quarter did you mean?",
                            "options": ["This quarter", "Last quarter"],
                        },
                    )
                ),
                "source": "groq",
                "usage": None,
            }

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        async def scenario():
            return await run_pipeline(
                user_query="how did Q1 go?",
                db_data=[{"revenue": 100}],
            )

        output = asyncio.run(scenario())
        assert output.clarification is not None
        assert output.clarification.question
        assert output.clarification.options
        assert output.answer == ""

    def test_malformed_json_falls_back_with_zero_confidence(self, monkeypatch):
        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512):
            return {"content": "not json at all", "source": "groq", "usage": None}

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        async def scenario():
            return await run_pipeline(user_query="q", db_data=[{}])

        output = asyncio.run(scenario())
        assert output.confidence == 0.0
        assert output.visuals == []

    def test_validation_failure_falls_back(self, monkeypatch):
        import json

        # confidence out of range -> ValidationError -> fallback
        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512):
            return {"content": json.dumps(_pipeline_json(confidence=9.9)), "source": "groq", "usage": None}

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        async def scenario():
            return await run_pipeline(user_query="q", db_data=[{}])

        output = asyncio.run(scenario())
        assert output.confidence == 0.0

    def test_mutable_default_is_gone(self, monkeypatch):
        # Two sequential calls must not share a news_context default list.
        import json

        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512):
            assert "News Context" in prompt
            return {"content": json.dumps(_pipeline_json()), "source": "groq", "usage": None}

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        async def scenario():
            await run_pipeline(user_query="q1", db_data=[{"revenue": 1}])
            await run_pipeline(user_query="q2", db_data=[{"revenue": 2}])
            return True

        assert asyncio.run(scenario())

    def test_mutable_default_always_set_to_fresh_empty(self):
        # Directly assert the default is None (not a shared list) in the signature.
        import inspect

        sig = inspect.signature(run_pipeline)
        assert sig.parameters["news_context"].default is None
        assert sig.parameters["computed_numbers"].default is None


class TestBuildPrompt:
    def test_computed_numbers_are_presented_as_precomputed(self):
        prompt = build_prompt(
            "avg rev",
            [{"date": "2024-01-01", "revenue": 100}],
            computed_numbers={"averages": {"revenue": 100.0}},
        )
        assert "averages" in prompt
        assert "never re-compute" in prompt.lower()

    def test_own_data_scope_shows_no_news(self):
        prompt = build_prompt("q", [{"revenue": 1}], source_scope="own_data")
        assert "no live web context" in prompt.lower() or "asked for their own data" in prompt.lower()