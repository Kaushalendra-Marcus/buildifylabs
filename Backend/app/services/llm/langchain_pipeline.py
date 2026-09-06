"""Structured AI insight/visual pipeline (specs/06 §3 contract, Phase B4).

Migrated from the old 9-fictional-visual contract to the 7 real types that
exist as frontend components (`metric`, `graph`, `table`, `comparison`,
`insight`, `alert`, `status`), with `props` instead of `chart_data`, a bounded
`confidence` (0..1), and the `clarification` alternate-response mode (specs/06
FR7 / specs/10 §2 "ask, don't guess").

Design rules this module honors:
- specs/11 §2 - the LLM never does arithmetic. Deterministic numbers arrive as
  `computed_numbers` (from `app/services/data/stats.py`) and the prompt asks
  the model only to *narrate* them, never to compute its own.
- specs/10 §2 - `root_causes`/`recommendations` use hedged causal language
  (enforced in SYSTEM_PROMPT); every answer is traceable to its SQL + raw row
  slice (fields the route fills in, never the LLM).
- specs/06 edge case 6 - large `db_data` never blows the context window: rows
  are truncated (plus a summarizing note) before the prompt is built.
"""
import json
import logging
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError

from app.services.llm.groq_service import generate_response

logger = logging.getLogger(__name__)

# The 7 visual types that actually exist as frontend components (specs/06 FR3).
# The authoritative per-type `props` shape is `src/lib/schemas/visuals.ts` in
# the frontend - the backend only constrains the type values.
VISUAL_TYPES = Literal[
    "metric", "graph", "table", "comparison", "insight", "alert", "status"
]
SOURCE_SCOPES = Literal["own_data", "live_web", "both"]

# Hard cap on how many rows are serialized into the prompt (specs/06 edge case
# 6: a large dataset must not blow the model's context window). The executor
# already enforces a SQL LIMIT, so rows coming in are bounded; this is defense
# in depth against that cap being raised later.
PROMPT_MAX_ROWS = 50


class VisualOutput(BaseModel):
    visual_type: VISUAL_TYPES = Field(
        ..., description="One of the 7 real frontend visual types."
    )
    # Shape depends on visual_type - see src/lib/schemas/visuals.ts (single
    # source of truth) for the authoritative per-type prop schema.
    props: Dict
    title: str


class ClarificationRequest(BaseModel):
    question: str
    options: List[str]


class WebSource(BaseModel):
    title: str
    url: str
    provider: str
    retrieved_at: str


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


SYSTEM_PROMPT = """You are a business intelligence analyst for a non-technical business owner.

You will receive:
- a plain-English User Query
- Business Data (the raw rows returned by an executed query against the user's own data)
- Computed Statistics (numbers ALREADY calculated by deterministic code)
- Web Search Results (fresh results retrieved from the internet for live-web queries)

Your job: answer the query with deep reasoning, and return a strict JSON object.

ALLOWED visual_type values (exactly these 7 - the real frontend components):
- metric       → a single headline number    props: {"label": str, "value": number, "change_pct": number|null, "direction": "up"|"down"|"flat"}
- graph        → a chart over a series       props: {"chart_type": "line"|"bar"|"pie"|"area", "labels": [str], "datasets": [{"name": str, "values": [number]}]}
- table        → structured rows             props: {"values": [[str|number]]}  and "columns": [str]
- comparison   → two things side by side     props: {"value": number, "baseline": number, "groups": [{"label": str, "value": number}]}
- insight      → a highlighted observation   props: {"text": str, "context": str}
- alert        → an anomaly / warning        props: {"level": "info"|"warning"|"critical", "summary": str, "reason": str}
- status       → an overall status badge     props: {"state": "on_track"|"at_risk"|"off_track", "detail": str}

STRICT RULES:
- Return ONLY valid JSON. No prose or markdown outside the JSON.
- THE COMPUTED STATISTICS ARE ALREADY CALCULATED - NEVER perform your own arithmetic.
  Quote these numbers where relevant; never invent others.
- NEVER guess data - only use what is provided. If the question is ambiguous or
    the evidence is insufficient, ask one focused clarification question instead
    of giving a vague summary. Missing details may include entities, scope, date
    range, metric, units, output format, or source. Use options when useful.
- Confidence must be between 0.0 and 1.0; 0.0 means not confident, 1.0 fully.
- root_causes and recommendations MUST use hedged causal language:
  "a possible contributing factor", "correlates with", "suggests" - never "the reason was"
  or "this caused" (specs/10 §2). Causal claims are hypotheses, not facts.
- CLARIFICATION RULE: Ask for clarification when the user's requested output
    is ambiguous OR the retrieved evidence is insufficient to answer it reliably.
    This applies to live-web questions too. Do not make up missing benchmark
    scores, dates, or metrics just to avoid asking. For a clear question with
    sufficient evidence, answer directly.
  Return clarification ONLY with: {"question": "...", "options": ["a", "b", "c"]}.
  Otherwise clarification must be null.
- LIVE WEB SOURCE RULE: When the query is live web, use Web Search Results as the
    source of factual claims. Do not mention, compare against, or apologize about
    missing user data. Do not say "the provided dataset" or "based on publicly
    available information" unless that wording is directly supported by a result.
    Answer the user's question directly and include the relevant current figures.
- CHART RULE: If the user asks for a chart, graph, or chart form, return a graph
    visual using the supplied verified series. Never omit a supplied company or
    fabricate a series.
- COMPLETE-SCOPE RULE: If the user asks for all available results, compare every
    relevant item and metric actually present in Web Search Results. Do not silently
    reduce a broad request to one example.
- If no visual fits, return an empty visuals list "".

Return this exact JSON:
{
  "answer": "...",
  "visuals": [
    {"visual_type": "metric", "props": {}, "title": "..."}
  ],
  "insights": ["..."],
  "summary": "...",
  "root_causes": ["..."],
  "recommendations": ["..."],
  "news_context": [],
  "anomalies": [],
  "confidence": 0.8,
  "clarification": null
}
"""


def build_prompt(
    user_query: str,
    db_data: Sequence[dict],
    computed_numbers: Optional[dict] = None,
    news_context: Optional[list] = None,
    source_scope: SOURCE_SCOPES = "own_data",
    company_name: Optional[str] = None,
) -> str:
    """Assemble the pipeline prompt (data + precomputed stats + context).

    The model only sees truncated rows + code-computed statistics, so it can
    narrate but never needs to calculate (specs/11 §2).
    """
    news_context = news_context or []
    computed_numbers = computed_numbers or {}

    rows, truncation_note = _truncate_rows(db_data)
    data_section = "\n".join(json.dumps(rows, indent=2, default=str)) + truncation_note

    if source_scope == "live_web":
        data_section = "Not applicable - this is a live web query; do not discuss user data."

    computed_section = (
        json.dumps(computed_numbers, indent=2, default=str) or "None"
    )

    if news_context:
        news_section = "\n".join(news_context)
    elif source_scope == "own_data":
        news_section = "User asked for their own data only - no live web context."
    else:
        news_section = "User is asking for general knowledge (live web) - use your knowledge to provide direct answers, do not ask for clarifications."

    company_section = company_name or "Not provided"

    clarification_guidance = (
        "IMPORTANT LIVE WEB INSTRUCTIONS: Search results are the factual source for this answer. "
        "Answer directly from those results and do not discuss the user's dataset. "
        "If the requested comparison cannot be established from the results, ask a focused "
        "clarification question about the missing entities, scope, metric, date range, or "
        "source instead of giving a vague conclusion. Never invent a figure."
        if source_scope == "live_web"
        else ""
    )

    return f"""
User Query:
{user_query}

Company (context only):
{company_section}

Business Data (rows returned by the executed SQL):
{data_section}

Computed Statistics (already calculated by code - narrate these, never re-compute):
{computed_section}

Web Search Results:
{news_section}

{clarification_guidance}

Based on the above, respond strictly in the JSON schema in the system prompt.
"""


def _truncate_rows(
    rows: Sequence[dict], max_rows: int = PROMPT_MAX_ROWS
) -> tuple[list, str]:
    """Cap the rows serialized into the prompt (specs/06 edge case 6).

    Returns (truncated_rows, note) where note is an empty string when nothing
    was cut and otherwise tells the model how many rows it can't see.
    """
    rows = list(rows)
    total = len(rows)
    if total <= max_rows:
        return rows, ""
    return rows[:max_rows], (
        f"\n[Showing the first {max_rows} of {total} rows only - use the "
        "Computed Statistics section for the full picture.]"
    )


def extract_json(text: str) -> dict:
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from LLM response: {text[:200]}")


def normalize_pipeline_payload(payload: dict) -> dict:
    """Fill omitted collection fields from otherwise usable model JSON."""
    defaults = {
        "answer": "",
        "visuals": [],
        "insights": [],
        "summary": "",
        "root_causes": [],
        "recommendations": [],
        "news_context": [],
        "web_sources": [],
        "anomalies": [],
        "confidence": 0.0,
        "clarification": None,
        "sql_query": None,
        "data_preview": None,
        "query_log_id": None,
    }
    normalized = {**defaults, **payload}
    for field in (
        "visuals",
        "insights",
        "root_causes",
        "recommendations",
        "news_context",
        "web_sources",
        "anomalies",
    ):
        if normalized[field] is None:
            normalized[field] = []
    return normalized


def fallback_output(reason: str, confidence: float = 0.0) -> PipelineOutput:
    return PipelineOutput(
        answer=reason,
        visuals=[],
        insights=[],
        summary="",
        root_causes=[],
        recommendations=[],
        news_context=[],
        anomalies=[],
        confidence=confidence,
    )


async def run_pipeline(
    user_query: str,
    db_data: Sequence[dict],
    computed_numbers: Optional[dict] = None,
    news_context: Optional[list] = None,
    source_scope: SOURCE_SCOPES = "own_data",
    company_name: Optional[str] = None,
) -> PipelineOutput:
    """Run the full narrative: prompt -> LLM -> validated PipelineOutput.

    Deterministic numbers are always precomputed by the caller and passed in;
    this function only narrates them. Falls back to a low-confidence generic
    PipelineOutput (never raises) on any malformed/validation failure
    (specs/06 FR4).
    """
    if news_context is None:
        news_context = []
    if computed_numbers is None:
        computed_numbers = {}

    try:
        prompt = build_prompt(
            user_query,
            db_data,
            computed_numbers,
            news_context,
            source_scope,
            company_name,
        )

        result = await generate_response(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=2000,
        )

        raw_output = result["content"]
        logger.info(f"LLM source used: {result.get('source', 'unknown')}")

        parsed = normalize_pipeline_payload(extract_json(raw_output))

        output = PipelineOutput(**parsed)

        return output

    except ValidationError as ve:
        logger.error(f"Schema validation failed: {ve}")
        return fallback_output(
            reason="Sorry, I could not process your request properly. Please try again.",
            confidence=0.0,
        )

    except ValueError as ve:
        logger.error(f"JSON extraction failed: {ve}")
        return fallback_output(
            reason="I had trouble understanding the data. Please rephrase your query.",
            confidence=0.0,
        )

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        return fallback_output(
            reason="Something went wrong. Please try again.",
            confidence=0.0,
        )