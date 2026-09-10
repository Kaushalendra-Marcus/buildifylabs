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
    Decision,
    PipelineOutput,
    VisualOutput,
    build_prompt,
    detect_preferred_visual,
    judge_sufficiency,
    sanitize_citations,
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

        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
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

        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
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

    def test_clarification_with_null_options_stays_clarification(self, monkeypatch):
        import json

        # Live bug: Groq returned {"question": ..., "options": null}, which
        # failed PipelineOutput validation and degraded to a generic fallback
        # instead of showing the clarification question. None coerces to [].
        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
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
                            "question": "Which AI business should I compare?",
                            "options": None,
                        },
                    )
                ),
                "source": "groq",
                "usage": None,
            }

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        async def scenario():
            return await run_pipeline(
                user_query="which ai business is best?",
                db_data=[{"revenue": 100}],
            )

        output = asyncio.run(scenario())
        assert output.clarification is not None
        assert output.clarification.question == "Which AI business should I compare?"
        assert output.clarification.options == []
        assert output.answer == ""

    def test_malformed_json_falls_back_with_zero_confidence(self, monkeypatch):
        # Garbage narration AND an empty rescue reply: nothing honest to say,
        # so the generic fallback stands (confidence 0.0, no visuals).
        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
            if "plain sentences" in system_prompt:
                return {"content": "   ", "source": "groq", "usage": None}
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
        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
            return {"content": json.dumps(_pipeline_json(confidence=9.9)), "source": "groq", "usage": None}

        monkeypatch.setattr(pipeline_mod, "generate_response", fake_generate)

        async def scenario():
            return await run_pipeline(user_query="q", db_data=[{}])

        output = asyncio.run(scenario())
        assert output.confidence == 0.0

    def test_mutable_default_is_gone(self, monkeypatch):
        # Two sequential calls must not share a news_context default list.
        import json

        async def fake_generate(prompt, system_prompt, temperature=0.2, max_tokens=512, **kwargs):
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


def _sequenced_fake(*contents):
    """Fake generate_response serving one reply per call, in order. An
    Exception entry raises instead (to simulate a failing judge call)."""
    import json

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


ROWS = [
    {"created_at": "2024-01-01", "revenue": 100, "region": "east"},
    {"created_at": "2024-01-02", "revenue": 250, "region": "west"},
    {"created_at": "2024-01-03", "revenue": 175, "region": "east"},
]

COMPUTED = {
    "row_count": 3,
    "averages": {"revenue": 175.0},
    "totals": {"revenue": 525.0},
}


class TestDecisionLoop:
    """The decision-driven loop: judge verdict -> narration -> guarantee,
    with the anti-repeat backstop. All generic, no per-question hardcoding."""

    def test_judge_clarify_routes_to_clarification(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(
                    decision="clarify",
                    missing="metric choice",
                    suggested_options=["revenue", "orders"],
                ),
                _pipeline_json(
                    answer="",
                    insights=[],
                    summary="",
                    root_causes=[],
                    recommendations=[],
                    confidence=0.0,
                    clarification={
                        "question": "Which metric should I use?",
                        "options": ["revenue", "orders"],
                    },
                ),
            ),
        )

        output = asyncio.run(
            run_pipeline(user_query="how are we doing?", db_data=ROWS)
        )
        assert output.clarification is not None
        assert output.clarification.question == "Which metric should I use?"
        assert output.clarification.options == ["revenue", "orders"]

    def test_judge_failure_fails_open_to_answer(self, monkeypatch):
        async def boom(prompt, system_prompt, temperature=0.0, max_tokens=400):
            raise RuntimeError("judge transport down")

        monkeypatch.setattr(pipeline_mod, "generate_response", boom)

        decision = asyncio.run(
            judge_sufficiency(
                user_query="q",
                source_scope="own_data",
                evidence={"row_count": 1},
            )
        )
        assert isinstance(decision, Decision)
        assert decision.decision == "answer"

    def test_empty_visuals_get_synthesized_from_real_rows(self, monkeypatch):
        # Narration returns a naked answer; the guarantee must chart/table it
        # from the actual rows - values traceable, nothing invented.
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(
                    visual_plan=[{"kind": "graph", "spec": "revenue over time"}]
                ),
                _pipeline_json(visuals=[], confidence=0.7),
            ),
        )

        output = asyncio.run(
            run_pipeline(
                user_query="how is revenue trending?",
                db_data=ROWS,
                computed_numbers=COMPUTED,
            )
        )
        assert output.clarification is None
        kinds = [visual.visual_type for visual in output.visuals]
        assert "graph" in kinds and "table" in kinds
        graph = next(v for v in output.visuals if v.visual_type == "graph")
        assert graph.props["chart_type"] == "line"
        assert graph.props["labels"] == ["2024-01-01", "2024-01-02", "2024-01-03"]
        assert graph.props["datasets"][0]["values"] == [100, 250, 175]
        table = next(v for v in output.visuals if v.visual_type == "table")
        assert table.props["columns"] == ["created_at", "revenue", "region"]

    def test_category_rows_synthesize_bar_chart(self, monkeypatch):
        rows = [
            {"region": "east", "revenue": 100},
            {"region": "west", "revenue": 250},
        ]
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.6)
            ),
        )

        output = asyncio.run(run_pipeline(user_query="sales by region", db_data=rows))
        graph = next(v for v in output.visuals if v.visual_type == "graph")
        assert graph.props["chart_type"] == "bar"
        assert graph.props["labels"] == ["east", "west"]
        assert graph.props["datasets"][0]["values"] == [100, 250]

    def test_repeat_clarification_forces_best_effort_answer(self, monkeypatch):
        prior = "What specific data would you like to see visualized in a chart?"
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    answer="",
                    confidence=0.0,
                    clarification={"question": prior, "options": []},
                ),
                _pipeline_json(
                    answer="Best-effort answer with assumptions stated.",
                    visuals=[],
                    confidence=0.5,
                ),
            ),
        )

        output = asyncio.run(
            run_pipeline(
                user_query="bar chart",
                db_data=[],
                prior_clarification=prior,
            )
        )
        # The loop is broken: an answer, never the same question twice.
        assert output.clarification is None
        assert "Best-effort" in output.answer

    def test_chart_followup_resolves_from_prior_rows(self, monkeypatch):
        prior_data = {
            "columns": ["created_at", "revenue", "region"],
            "row_count": 3,
            "rows": ROWS,
            "from_query": "how is revenue?",
        }
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(decision="answer", chart_from_prior=True),
                _pipeline_json(answer="Here is the chart.", visuals=[], confidence=0.7),
            ),
        )

        output = asyncio.run(
            run_pipeline(
                user_query="show in chart form",
                db_data=[],
                prior_data=prior_data,
            )
        )
        assert output.clarification is None
        kinds = [visual.visual_type for visual in output.visuals]
        assert "graph" in kinds

    def test_market_series_synthesize_generic_graph(self, monkeypatch):
        market = [
            {
                "entity": "Acme",
                "labels": ["Jan 01", "Jan 02", "Jan 03"],
                "values": [10.0, 11.0, 12.0],
            },
            {
                "entity": "Globex",
                "labels": ["Jan 01", "Jan 02", "Jan 03"],
                "values": [20.0, 19.0, 21.0],
            },
        ]
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )

        output = asyncio.run(
            run_pipeline(
                user_query="compare performance",
                db_data=[],
                source_scope="live_web",
                market_data=market,
            )
        )
        assert len(output.visuals) == 1
        graph = output.visuals[0]
        assert graph.visual_type == "graph"
        assert graph.props["chart_type"] == "line"
        assert [dataset["name"] for dataset in graph.props["datasets"]] == [
            "Acme",
            "Globex",
        ]
        # Generic title from the entities - never a hardcoded stock story.
        assert "Acme" in graph.title and "Globex" in graph.title


class TestRobustnessLoop:
    """Live-hardening: empty-completion retries, requested shapes, citations,
    the thinking trace, and the sources-table guarantee."""

    def test_judge_empty_then_valid_recovers(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                "",
                _decision_json(
                    decision="clarify",
                    missing="metric choice",
                    suggested_options=["revenue"],
                ),
                _pipeline_json(
                    answer="",
                    insights=[],
                    summary="",
                    root_causes=[],
                    recommendations=[],
                    confidence=0.0,
                    clarification={
                        "question": "Which metric?",
                        "options": ["revenue"],
                    },
                ),
            ),
        )

        output = asyncio.run(
            run_pipeline(user_query="how are we doing?", db_data=ROWS)
        )
        assert output.clarification is not None
        assert output.clarification.question == "Which metric?"

    def test_narration_empty_retries_instead_of_fallback(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                "",
                _pipeline_json(
                    answer="Recovered on retry.",
                    visuals=[],
                    insights=[],
                    summary="",
                    root_causes=[],
                    recommendations=[],
                    confidence=0.6,
                ),
            ),
        )

        output = asyncio.run(run_pipeline(user_query="q", db_data=[]))
        assert output.answer == "Recovered on retry."
        assert output.clarification is None

    def test_requested_bar_shape_overrides_line_default(self, monkeypatch):
        # Date series default to line; "as a bar chart" must win.
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )

        output = asyncio.run(
            run_pipeline(user_query="show revenue as a bar chart", db_data=ROWS)
        )
        graph = next(v for v in output.visuals if v.visual_type == "graph")
        assert graph.props["chart_type"] == "bar"
        assert graph.props["labels"] == ["2024-01-01", "2024-01-02", "2024-01-03"]

    def test_requested_table_shape_leads(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )

        output = asyncio.run(
            run_pipeline(user_query="give it to me in a table", db_data=ROWS)
        )
        assert output.visuals[0].visual_type == "table"

    def test_detect_preferred_visual_shapes(self):
        assert detect_preferred_visual("show as a bar chart") == "bar"
        assert detect_preferred_visual("plot a line chart") == "line"
        assert detect_preferred_visual("pie chart please") == "pie"
        assert detect_preferred_visual("in a table") == "table"
        assert detect_preferred_visual("how is revenue?") is None

    def test_citations_keep_valid_drop_phantom(self):
        answer = "Raised $50M in 2024 [1] and hired a lot [9]."
        assert sanitize_citations(answer, 2) == "Raised $50M in 2024 [1] and hired a lot ."
        assert sanitize_citations("No markers here.", 2) == "No markers here."
        assert sanitize_citations("Claim [1].", 0) == "Claim ."

    def test_narration_citations_sanitized_live(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    answer="Acme raised $50M [1] and Globex raised $70M [9].",
                    visuals=[],
                    confidence=0.7,
                ),
            ),
        )

        output = asyncio.run(
            run_pipeline(
                user_query="startup funding news",
                db_data=[],
                source_scope="live_web",
                news_context=["Acme funding snippet", "Globex snippet"],
            )
        )
        assert "[1]" in output.answer
        assert "[9]" not in output.answer

    def test_thinking_trace_present_on_answers(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )

        output = asyncio.run(
            run_pipeline(user_query="how is revenue?", db_data=ROWS)
        )
        assert output.thinking
        assert any("Judged" in step for step in output.thinking)
        assert any("Visuals out" in step for step in output.thinking)

    def test_no_sources_table_visual_for_web_answer(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )
        sources = [
            {"title": "Acme raises", "url": "https://a.example", "provider": "X"},
            {"title": "Globex launches", "url": "https://b.example", "provider": "Y"},
        ]

        output = asyncio.run(
            run_pipeline(
                user_query="startup news",
                db_data=[],
                source_scope="live_web",
                news_context=["snippet one", "snippet two"],
                web_sources=sources,
            )
        )
        tables = [v for v in output.visuals if v.visual_type == "table"]
        # No "Sources cited" table visual: sources surface once, via the
        # expandable sources section (web_sources), never as a duplicate card.
        assert all(table.title != "Sources cited" for table in tables)

    def test_model_drafted_sources_table_is_stripped(self, monkeypatch):
        # The model may still draft a sources table from old habits: the
        # pipeline must strip it — sources render once, expandable below.
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    visuals=[
                        {
                            "visual_type": "table",
                            "title": "Sources cited",
                            "props": {
                                "columns": ["Source"],
                                "values": [["Acme raises"], ["Globex launches"]],
                            },
                        }
                    ],
                    confidence=0.7,
                ),
            ),
        )

        output = asyncio.run(
            run_pipeline(
                user_query="startup news",
                db_data=[],
                source_scope="live_web",
                news_context=["snippet one", "snippet two"],
                web_sources=[
                    {"title": "Acme raises", "url": "https://a.example", "provider": "X"},
                    {"title": "Globex launches", "url": "https://b.example", "provider": "Y"},
                ],
            )
        )
        assert all(
            not (
                v.visual_type == "table"
                and str(v.title or "").strip().lower() == "sources cited"
            )
            for v in output.visuals
        )

    def test_empty_options_backfilled_from_judge(self, monkeypatch):
        # Both affordances, always: the narrator left options empty, so the
        # judge's evidence-grounded suggestions fill the pills.
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(
                    decision="clarify",
                    missing="metric choice",
                    suggested_options=["total users", "active users"],
                ),
                _pipeline_json(
                    answer="",
                    insights=[],
                    summary="",
                    root_causes=[],
                    recommendations=[],
                    confidence=0.0,
                    clarification={
                        "question": "Which user metric?",
                        "options": [],
                    },
                ),
            ),
        )

        output = asyncio.run(
            run_pipeline(user_query="compare agents by users", db_data=[])
        )
        assert output.clarification is not None
        assert output.clarification.options == ["total users", "active users"]

    def test_empty_options_stays_type_only_when_judge_has_none(self, monkeypatch):
        # Genuinely open-ended: no suggestion anywhere keeps [] so the UI
        # renders the free-text box alone.
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(decision="clarify", missing="free-form detail"),
                _pipeline_json(
                    answer="",
                    insights=[],
                    summary="",
                    root_causes=[],
                    recommendations=[],
                    confidence=0.0,
                    clarification={
                        "question": "Describe what you need?",
                        "options": [],
                    },
                ),
            ),
        )

        output = asyncio.run(run_pipeline(user_query="help me", db_data=[]))
        assert output.clarification is not None
        assert output.clarification.options == []


class TestFollowups:
    def test_followups_capped_cleaned(self):
        from app.services.llm.langchain_pipeline import normalize_pipeline_payload

        payload = normalize_pipeline_payload(
            {"followups": ["  drill down? ", "", 42, "compare regions?"]}
        )
        assert payload["followups"] == ["drill down?", "42", "compare regions?"][:3]

    def test_followups_non_list_becomes_empty(self):
        from app.services.llm.langchain_pipeline import normalize_pipeline_payload

        assert normalize_pipeline_payload({"followups": None})["followups"] == []
        assert normalize_pipeline_payload({})["followups"] == []

    def test_run_pipeline_carries_model_followups(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                _pipeline_json(
                    visuals=[],
                    confidence=0.7,
                    followups=["Break it down by region?", "Compare to last month?"],
                ),
            ),
        )

        output = asyncio.run(run_pipeline(user_query="how is revenue?", db_data=ROWS))
        assert output.followups == [
            "Break it down by region?",
            "Compare to last month?",
        ]

    def test_prose_rescue_answers_from_evidence(self, monkeypatch):
        # Structured narration fails but rows exist: plain-text rescue over
        # the real evidence instead of a dead-end fallback.
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                "not json at all",
                "Revenue held steady across the three days.",
            ),
        )

        output = asyncio.run(
            run_pipeline(user_query="how is revenue?", db_data=ROWS)
        )
        assert output.answer == "Revenue held steady across the three days."
        assert output.confidence == 0.35
        assert output.clarification is None
        assert any("rescue" in step for step in output.thinking)

    def test_prose_rescue_refuses_json_blob(self, monkeypatch):
        # A rescue reply that is itself JSON must not be presented as prose.
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(),
                "not json at all",
                '{"answer": "sneaky"}',
            ),
        )

        output = asyncio.run(run_pipeline(user_query="q", db_data=ROWS))
        assert output.confidence == 0.0
        assert output.answer != '{"answer": "sneaky"}'

    def test_prose_rescue_none_without_evidence(self, monkeypatch):
        from app.services.llm.langchain_pipeline import _narrate_prose_rescue

        async def scenario():
            return await _narrate_prose_rescue("q", [], [], [])

        assert asyncio.run(scenario()) is None

class TestEvidenceConfidence:
    def _answer_run(self, monkeypatch, narration, **kwargs):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(_decision_json(), narration),
        )
        return asyncio.run(run_pipeline(user_query="q", **kwargs))

    def test_thin_evidence_caps_high_confidence(self, monkeypatch):
        output = self._answer_run(
            monkeypatch,
            _pipeline_json(answer="One fact.", visuals=[], confidence=0.9),
            db_data=[],
            source_scope="live_web",
            news_context=["only snippet"],
        )
        assert output.confidence == 0.65

    def test_rich_evidence_keeps_confidence(self, monkeypatch):
        output = self._answer_run(
            monkeypatch,
            _pipeline_json(answer="Solid.", visuals=[], confidence=0.85),
            db_data=ROWS,
            computed_numbers=COMPUTED,
        )
        assert output.confidence == 0.85

    def test_no_evidence_caps_at_floor(self, monkeypatch):
        output = self._answer_run(
            monkeypatch,
            _pipeline_json(answer="Guessy.", visuals=[], confidence=0.8),
            db_data=[],
        )
        assert output.confidence == 0.35

    def test_stages_emitted_in_order(self, monkeypatch):
        stages: list[str] = []

        async def on_stage(stage: str) -> None:
            stages.append(stage)

        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )
        asyncio.run(
            run_pipeline(user_query="how is revenue?", db_data=ROWS, on_stage=on_stage)
        )
        assert stages == ["judging", "narrating", "visuals"]

    def test_failing_stage_callback_never_breaks_run(self, monkeypatch):
        async def boom(stage: str) -> None:
            raise RuntimeError("listener down")

        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )
        output = asyncio.run(
            run_pipeline(user_query="how is revenue?", db_data=ROWS, on_stage=boom)
        )
        assert output.answer


class TestCitedFigures:
    SNIPPETS = [
        "HeadshotPro earned $300k per month while solo.",
        "Headlime was sold for $1M just eight months after launch.",
        "Explore 30 practical ideas over 8 months of work in 2024.",
    ]

    def test_extracts_money_only_not_bare_numbers_or_years(self):
        from app.services.llm.langchain_pipeline import _figures_from_snippets

        figures = _figures_from_snippets(self.SNIPPETS)
        texts = [figure["text"] for figure in figures]
        assert "$300k" in texts
        assert "$1M" in texts
        # "30 ideas", "8 months", "2024" carry no unit and must not qualify.
        assert not any("30" in text and "$" not in text for text in texts)
        assert not any("2024" in text for text in texts)
        assert all(figure["unit"] == "money" for figure in figures)

    def test_extracts_percents(self):
        from app.services.llm.langchain_pipeline import _figures_from_snippets

        figures = _figures_from_snippets(["Growth hit 68% in Q1, up from 40%."])
        assert [(f["text"], f["value"]) for f in figures] == [
            ("68%", 68.0),
            ("40%", 40.0),
        ]

    def test_bar_normalizes_kilos_and_millions(self, monkeypatch):
        monkeypatch.setattr(
            pipeline_mod,
            "generate_response",
            _sequenced_fake(
                _decision_json(), _pipeline_json(visuals=[], confidence=0.7)
            ),
        )

        output = asyncio.run(
            run_pipeline(
                user_query="ai revenue examples",
                db_data=[],
                source_scope="live_web",
                news_context=self.SNIPPETS,
                web_sources=[],
            )
        )
        kinds = [visual.visual_type for visual in output.visuals]
        assert "graph" in kinds
        graph = next(v for v in output.visuals if v.visual_type == "graph")
        assert graph.props["chart_type"] == "bar"
        assert graph.props["datasets"][0]["values"] == [300000.0, 1000000.0]
        tables = [v for v in output.visuals if v.visual_type == "table"]
        assert tables[0].title == "Figures cited"

    def test_no_figures_no_figures_visuals(self, monkeypatch):