"""Pydantic contracts: the 7 visual types + pipeline I/O. Split from langchain_pipeline.py; behavior unchanged."""

import logging

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# The 7 visual types that actually exist as frontend components (specs/06 FR3).
# The authoritative per-type `props` shape is `src/lib/schemas/visuals.ts` in
# the frontend - the backend only constrains the type values.
VISUAL_TYPES = Literal[
    "metric", "graph", "table", "comparison", "insight", "alert", "status"
]
SOURCE_SCOPES = Literal["own_data", "live_web", "both"]


class VisualOutput(BaseModel):
    visual_type: VISUAL_TYPES = Field(
        ..., description="One of the 7 real frontend visual types."
    )
    # Shape depends on visual_type - see src/lib/schemas/visuals.ts (single
    # source of truth) for the authoritative per-type prop schema.
    props: Dict
    title: str
    # Visual provenance contract (first-class pipeline stage): every visual
    # carries requested intent, entities, metric, units, timeframe,
    # frequency, evidence/source IDs, underlying data points, and
    # computation IDs where applicable. Validated before rendering; a
    # visual whose entity/metric/timeframe/units/source/values cannot be
    # traced to validated evidence/computation is rejected (fail closed).
    provenance: Optional[Dict] = Field(default=None)

    model_config = {"extra": "allow"}


class ClarificationRequest(BaseModel):
    question: str
    # The model sometimes emits `"options": null` when it has no preset
    # options (seen live: Groq clarification with options None). Without
    # coercion that single null fails the whole PipelineOutput validation and
    # the user gets a dead-end generic fallback instead of the clarification
    # question. None (or a missing key) means "no preset options".
    options: List[str] = Field(default_factory=list)

    @field_validator("options", mode="before")
    @classmethod
    def _null_options_to_empty(cls, value):
        if value is None:
            return []
        return value


class WebSource(BaseModel):
    title: str
    url: str
    provider: str
    retrieved_at: str
    # Recency signal from Tavily (YYYY-MM-DD). Optional so older cached rows
    # and provider-only entries still validate.
    published_date: Optional[str] = None
    score: Optional[float] = None


class VisualPlanItem(BaseModel):
    """One visual the decision step wants, described generically: which of
    the 7 real visual types and which available field/series it should be
    built from. The narration step turns the plan into concrete visuals."""

    kind: Literal[
        "metric", "graph", "table", "comparison", "insight", "alert", "status"
    ]
    spec: str = ""
    chart_type: Optional[Literal["line", "bar", "pie", "area"]] = None


class Decision(BaseModel):
    """The sufficiency judge's verdict: answer with the evidence at hand, or
    ask exactly one clarification. `visual_plan` tells the narration step
    which visuals the evidence supports; `chart_from_prior` covers follow-ups
    like "show that in a chart" that refer to the previous answer's data;
    `preferred_visual` carries an explicitly requested output shape
    ("as a bar chart", "in a table") through to narration and synthesis.
    `tools_needed` is the tool-routing verdict (see `plan_tools`): which
    evidence adapters the query actually needs. Empty means "no opinion" -
    dispatch falls back to the deterministic predicates."""

    decision: Literal["answer", "clarify"]
    missing: str = ""
    chart_from_prior: bool = False
    visual_plan: List[VisualPlanItem] = Field(default_factory=list)
    suggested_options: List[str] = Field(default_factory=list)
    preferred_visual: Optional[str] = None
    tools_needed: List[str] = Field(default_factory=list)

    @field_validator(
        "visual_plan", "suggested_options", "tools_needed", mode="before"
    )
    @classmethod
    def _null_lists_to_empty(cls, value):
        if value is None:
            return []
        return value


def default_decision() -> Decision:
    """Fail-open verdict: answer with whatever evidence exists. Used whenever
    the judge call itself fails so a judge outage never blocks an answer."""
    return Decision(decision="answer")


class PipelineOutput(BaseModel):
    answer: str
    visuals: List[VisualOutput]
    insights: List[str]
    summary: str
    root_causes: List[str]
    recommendations: List[str]
    news_context: List[str]
    web_sources: List[WebSource] = Field(default_factory=list)
    anomalies: List[str]
    confidence: float = Field(ge=0.0, le=1.0)  # bounded - closed an old gap
    # Alternate response mode (specs/06 FR7): when populated, the other answer
    # fields are empty and the frontend renders this as a quick-pick prompt.
    clarification: Optional[ClarificationRequest] = None
    # specs/10 §2 traceability - the route fills these in after the pipeline,
    # never the LLM: the exact SQL that produced the answer, the raw row slice
    # it ran on, and the QueryLogs id so the UI can "show the query" and flag.
    sql_query: Optional[str] = None
    data_preview: Optional[List[Dict]] = None
    query_log_id: Optional[str] = None
    # Thinking trace: short machine-written steps of what the pipeline did
    # (judge verdict, tools run, visuals planned/synthesized). Rendered by
    # clients that want to show their work; ignored by those that don't.
    thinking: List[str] = Field(default_factory=list)
    # Suggested follow-up questions the user can tap to continue. Genuine
    # next questions about this answer, never repeats of it.
    followups: List[str] = Field(default_factory=list)
    # Compact structured research state (Phase 18): the canonical plan,
    # validated evidence summary, and source refs persisted per turn so
    # follow-ups retain entities/metrics/period instead of reverting to
    # incomplete evidence. Bounded and JSON-safe by construction.
    research_state: Optional[Dict[str, Any]] = None

__all__ = [
    "ClarificationRequest",
    "Decision",
    "PipelineOutput",
    "SOURCE_SCOPES",
    "VISUAL_TYPES",
    "VisualOutput",
    "VisualPlanItem",
    "WebSource",
    "default_decision",
]
