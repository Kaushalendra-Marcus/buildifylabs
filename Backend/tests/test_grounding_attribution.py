"""Attributed grounding + word-boundary retrieval + rank-before-budget.

Regression coverage for the hallucination-grounding hardening:
- same number in a different source does NOT ground a miscited claim
  (source-index match, not any-pool match; 1% visual / 5% prose kept)
- substring hits ("car" in "scar") never count as retrieval matches
- zero-hit document queries return [] (judge clarifies); only
  summarize-style queries keep the first_chunks fallback
- budget keeps the best-ranked pair, not the first-arrived one
"""

import asyncio
import uuid
from types import SimpleNamespace

from sqlalchemy.dialects import sqlite

import app.services.data.vector_store as vector_store_mod
from app.services.data.vector_store import (
    retrieve_document_evidence,
    search_chunks,
)
from app.services.llm.context_budget import (
    fit_pairs_to_budget,
    rank_snippet_pairs,
)
from app.services.llm.pipeline.grounding import (
    _attributed_snippet_values,
    _evidence_numbers_attributed,
    _prose_claims_grounded,
    _visual_numbers_grounded,
)
from app.services.llm.pipeline.models import VisualOutput


def _graph(values, labels=("a [1]", "b [2]")):
    return VisualOutput(
        visual_type="graph",
        props={
            "chart_type": "bar",
            "labels": list(labels),
            "datasets": [{"name": "amount", "values": list(values)}],
        },
        title="g",
    )


def _cited_graph(values, citation):
    label = f"item [{citation}]"
    return VisualOutput(
        visual_type="graph",
        props={
            "chart_type": "bar",
            "labels": [label],
            "datasets": [{"name": "amount", "values": list(values)}],
        },
        title="g",
    )


SNIPPETS = ["Acme raised $300k in 2024", "Globex sold for $1M"]


class TestAttributedVisualGrounding:
    def test_correct_source_accepted(self):
        assert _visual_numbers_grounded(_cited_graph([300000.0], 1), SNIPPETS)

    def test_same_number_wrong_source_rejected(self):
        # $300k exists (snippet 1) but the visual cites [2] which holds $1M.
        # Any-pool logic would accept; attributed grounding must reject.
        assert not _visual_numbers_grounded(_cited_graph([300000.0], 2), SNIPPETS)

    def test_second_source_correct_citation_accepted(self):
        assert _visual_numbers_grounded(_cited_graph([1000000.0], 2), SNIPPETS)

    def test_second_source_wrong_citation_rejected(self):
        assert not _visual_numbers_grounded(_cited_graph([1000000.0], 1), SNIPPETS)

    def test_uncited_visual_keeps_legacy_any_pool(self):
        visual = VisualOutput(
            visual_type="graph",
            props={
                "chart_type": "bar",
                "labels": ["a", "b"],
                "datasets": [{"name": "amount", "values": [300000.0]}],
            },
            title="g",
        )
        assert _visual_numbers_grounded(visual, SNIPPETS)

    def test_invented_magnitude_still_rejected(self):
        assert not _visual_numbers_grounded(_cited_graph([42000000.0], 1), SNIPPETS)
        assert not _visual_numbers_grounded(_cited_graph([42000000.0], 2), SNIPPETS)

    def test_numbers_carry_source_idx(self):
        attributed = _attributed_snippet_values(SNIPPETS)
        assert (300000.0, 1) in attributed
        assert (1000000.0, 2) in attributed
        pool = _evidence_numbers_attributed(
            snippets=SNIPPETS, rows=[{"revenue": 42.0}]
        )
        assert (300000.0, 1) in pool
        assert (42.0, 0) in pool  # structured numbers get idx 0


class TestAttributedProseGrounding:
    def test_correct_citation_accepted(self):
        assert _prose_claims_grounded("Acme raised $300k [1].", SNIPPETS)

    def test_same_number_wrong_citation_rejected(self):
        assert not _prose_claims_grounded("Acme raised $300k [2].", SNIPPETS)

    def test_uncited_number_rejected(self):
        assert not _prose_claims_grounded("Acme raised $300k.", SNIPPETS)

    def test_prose_keeps_5pct_tolerance(self):
        # 315k is 5% over 300k -> grounded; 330k (10%) is not.
        assert _prose_claims_grounded("Acme raised $315k [1].", SNIPPETS)
        assert not _prose_claims_grounded("Acme raised $330k [1].", SNIPPETS)

    def test_no_numbers_trivially_grounded(self):
        assert _prose_claims_grounded("Acme did well [1].", SNIPPETS)


# --- retrieval fakes (mirror test_vector_store scoping) ---

USER_ID = uuid.uuid4()
FILE_ID = uuid.uuid4()


def _compiled_sql(statement):
    return str(
        statement.compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class _Result:
    def __init__(self, mappings=None, scalar=None):
        self._mappings = mappings or []
        self._scalar = scalar

    def mappings(self):
        return self._mappings

    def scalar_one_or_none(self):
        return self._scalar


def _chunk(content, index=0):
    return {
        "content": content,
        "file_name": "report.pdf",
        "chunk_index": index,
        "created_at": "2026-09-01T00:00:00",
        "user_id": USER_ID,
        "file_id": FILE_ID,
    }


def _db_with_chunks(chunks):
    async def execute(statement, *args, **kwargs):
        mine = [c for c in chunks if c["user_id"] == USER_ID]
        if "document_chunks.id" in _compiled_sql(statement):
            return _Result(scalar=object() if mine else None)
        return _Result(mappings=[dict(c) for c in mine])

    return SimpleNamespace(execute=execute)


def _run(coro):
    return asyncio.run(coro)


class TestWordBoundaryRetrieval:
    def test_substring_does_not_match(self):
        db = _db_with_chunks([_chunk("The scar healed quickly.")])
        # Old haystack.count("car") matched inside "scar"; word-boundary must not.
        assert _run(search_chunks(db, USER_ID, "car", top_k=5)) == []

    def test_word_boundary_matches_and_keeps_score(self):
        db = _db_with_chunks([_chunk("Revenue was five million.")])
        rows = _run(search_chunks(db, USER_ID, "revenue?", top_k=5))
        assert [r["content"] for r in rows] == ["Revenue was five million."]
        assert isinstance(rows[0]["score"], int) and rows[0]["score"] > 0

    def test_zero_hits_returns_empty_for_judge(self):
        db = _db_with_chunks([
            _chunk("Revenue was five million.", index=0),
            _chunk("Costs held steady.", index=1),
        ])
        assert _run(retrieve_document_evidence(db, USER_ID, "penguins")) == ([], [])

    def test_summarize_keeps_first_chunks_fallback(self):
        db = _db_with_chunks([
            _chunk("Revenue was five million.", index=0),
            _chunk("Costs held steady.", index=1),
        ])
        texts, sources = _run(
            retrieve_document_evidence(db, USER_ID, "summarize my report")
        )
        assert texts == ["Revenue was five million.", "Costs held steady."]
        assert all(s["provider"] == "your_documents" for s in sources)


class TestRankBeforeBudget:
    def test_budget_keeps_best_ranked_not_first_arrived(self):
        query = "fuel prices market"
        texts = [
            "unrelated filler about cooking recipes pasta " + ("x" * 60),
            "fuel prices market surge with details " + ("y" * 60),
        ]
        sources = [
            {"title": "low", "url": "https://low.example", "provider": "X"},
            {
                "title": "high",
                "url": "https://high.example",
                "provider": "X",
                "score": 0.95,
                "published_date": "2026-10-05",
            },
        ]
        ranked_texts, ranked_sources = rank_snippet_pairs(texts, sources, query)
        # Best-ranked (high relevance) leads even though it arrived second.
        assert "fuel prices market surge" in ranked_texts[0]
        assert ranked_sources[0]["url"] == "https://high.example"
        # Budget fits exactly one item (either single cost, never both).
        budget = max(len(t) + 20 for t in texts)
        assert sum(len(t) + 20 for t in texts) > budget
        kept_texts, kept_sources, dropped = fit_pairs_to_budget(
            ranked_texts, ranked_sources, budget
        )
        assert len(kept_texts) == 1
        assert "fuel prices market surge" in kept_texts[0]
        assert dropped == 1
        # Without ranking, the same budget would keep the wrong (first) item.
        unranked_kept, _, _ = fit_pairs_to_budget(texts, sources, budget)
        assert len(unranked_kept) == 1
        assert "cooking recipes" in unranked_kept[0]

    def test_chat_calls_rank_before_fit_for_both_pools(self):
        import inspect

        import app.routes.chat as chat_mod

        source = inspect.getsource(chat_mod._answer_request)
        # Call sites use parens; the import line has none.
        assert source.count("rank_snippet_pairs(") >= 2, (
            "chat must rank both doc and web pools before budgeting"
        )
        assert source.count("fit_pairs_to_budget(") >= 2
        assert source.index("rank_snippet_pairs(") < source.index(
            "fit_pairs_to_budget("
        )


class TestSingleEntityExclusion:
    """Live incident: a single-company live_web answer flagged its own
    subject ('Amazon') as an excluded entity and its snippet-cited figure
    as an ungrounded number -- both thinking-trace false positives on a
    correct answer. Exclusion is comparison-shaped; single-entity subjects
    are unvalidated, never excluded. Snippet evidence belongs in the
    narration-contract pool."""

    def test_single_entity_never_self_excluded(self):
        from app.services.llm.pipeline.grounding import (
            build_validated_evidence_state,
        )

        state = build_validated_evidence_state(
            query="what is revenue of amazon in last year",
            plan={"entities": ["Amazon"], "metrics": ["revenue_growth"]},
            gate={"applies": False, "blocked": False},
            completeness=None,
        )
        assert state["excluded_entities"] == []

    def test_multi_entity_still_derives_exclusion(self):
        from app.services.llm.pipeline.grounding import (
            build_validated_evidence_state,
        )

        state = build_validated_evidence_state(
            query="Compare E1, E2 and E3 on revenue growth",
            plan={"entities": ["E1", "E2", "E3"], "metrics": ["revenue_growth"]},
            gate={"applies": False, "blocked": False,
                  "validated_entities": ["E1", "E2"]},
            completeness=None,
        )
        assert state["excluded_entities"] == ["E3"]

    def test_snippet_figure_passes_narration_contract(self):
        from app.services.llm.pipeline.grounding import (
            apply_narration_contract,
            build_validated_evidence_state,
        )
        from app.services.llm.pipeline.models import PipelineOutput

        state = build_validated_evidence_state(
            query="what does the report say about revenue?",
            plan={"entities": ["Acme"], "metrics": []},
            gate={"applies": False, "blocked": False},
            completeness=None,
        )
        output = PipelineOutput(
            answer="Acme revenue was 520 dollars [1].",
            visuals=[], insights=[], summary="", root_causes=[],
            recommendations=[], news_context=[], anomalies=[],
            confidence=0.7, clarification=None,
        )
        thinking: list = []
        out = apply_narration_contract(
            output, validated_state=state, computed_numbers={},
            gate={}, thinking=thinking,
            snippets=["Acme FY2024 report states revenue of 520 dollars."],
        )
        assert out.confidence == 0.7
        assert not any("outside validated evidence" in line for line in thinking)

    def test_completeness_subject_exclusion_filtered_for_single_entity(self):
        from app.services.llm.pipeline.grounding import (
            build_validated_evidence_state,
        )

        state = build_validated_evidence_state(
            query="what is revenue of amazon in last year",
            plan={"entities": ["Amazon"], "metrics": ["revenue_growth"]},
            gate={"applies": False, "blocked": False},
            completeness={
                "validated_entities": [],
                "excluded_entities": [
                    {"entity": "Amazon", "reason": "no validated evidence"}
                ],
            },
        )
        assert state["excluded_entities"] == []

    def test_subject_followups_survive_single_entity_answer(self):
        from app.services.data.canonical import ground_followups

        state = {"excluded_entities": [], "excluded_metrics": []}
        followups = [
            "Compare Amazon to Microsoft",
            "What is Amazon revenue growth over 5 years?",
        ]
        assert ground_followups(followups, state) == followups
