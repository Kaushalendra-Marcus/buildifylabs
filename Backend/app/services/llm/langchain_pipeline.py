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
import difflib
import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError, field_validator

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
    ("as a bar chart", "in a table") through to narration and synthesis."""

    decision: Literal["answer", "clarify"]
    missing: str = ""
    chart_from_prior: bool = False
    visual_plan: List[VisualPlanItem] = Field(default_factory=list)
    suggested_options: List[str] = Field(default_factory=list)
    preferred_visual: Optional[str] = None

    @field_validator("visual_plan", "suggested_options", mode="before")
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
  options MUST be a JSON array - use [] when you have no preset options, never null.
  Prefer 2-4 preset options grounded in the available evidence so the user can
  tap instead of type; [] is only for genuinely open-ended questions (the UI
  always offers a free-text box alongside the options).
  Otherwise clarification must be null.
- LIVE WEB SOURCE RULE: When the query is live web, use Web Search Results as the
    source of factual claims. Do not mention, compare against, or apologize about
    missing user data. Do not say "the provided dataset" or "based on publicly
    available information" unless that wording is directly supported by a result.
    Answer the user's question directly and include the relevant current figures.
- CHART RULE: If the user asks for a chart, graph, plot, or chart form, return a graph
    visual using supplied verified values (EVIDENCE ROWS, MARKET SERIES, or PRIOR DATA).
    Never omit a supplied series and never fabricate one. A follow-up that refers to a
    previous answer ("chart that", "show it as bars", "break it down") MUST be built from
    the PRIOR DATA section - never ask which data is meant.
- COMPLETE-SCOPE RULE: If the user asks for all available results, compare every
    relevant item and metric actually present in Web Search Results. Do not silently
    reduce a broad request to one example.
- VISUAL MANDATE: every normal answer MUST include at least one visual whenever    anything plottable exists (evidence rows, market series, or prior data). Choose
    by data shape, not by topic:
      a date-like column + a numeric column (3+ rows) -> line or area graph;
      a text column with 2-12 distinct values + a numeric column -> bar graph;
      one headline number -> metric card (with change/direction when growth stats exist);
      two periods of the same metric -> comparison card AND a table;
      a threshold breach or anomaly -> alert card;
      data freshness or sync metadata -> status card;
      otherwise, or additionally -> a table card of the real rows.
    All visual values MUST come from the supplied sections - never invent numbers to
    fill a chart. An empty visuals list is allowed ONLY for a purely qualitative
    answer with no plottable evidence at all; then include at least one insight
    card grounded in the cited snippets instead.
- CITATION RULE: Web Search Results are numbered ([1], [2], ...). Every factual
    claim taken from them MUST carry its source number inline, e.g. "raised $50M
    in 2024 [2]". Cite the exact snippet each fact came from; never cite a number
    that was not listed, and never invent sources. Claims from your own general
    knowledge need no marker - but prefer snippet-grounded claims whenever the
    snippets cover the point.
- PRIOR RESULT RULE: when PRIOR DATA is present and the query is a follow-up on it
    (chart it, filter it, compare it, explain a part of it), answer from PRIOR DATA.
    Do not claim the data is missing and do not re-ask what was already answered.
- CLARIFICATION DISCIPLINE: clarify only when the DECISION section says so. Ask
    exactly the decided question, never the PRIOR CLARIFICATION question restated,
    and ground every option in actually available evidence (columns, metrics, or
    the decided suggestions). A second clarification on the same point is forbidden:
    answer best-effort and state your assumptions instead.

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


DECISION_SYSTEM_PROMPT = """You are the decision step of a business-intelligence pipeline. You do NOT answer
the user. You judge whether the retrieved evidence suffices and plan the response.

You receive: the user query, an evidence inventory (what the tools actually
returned: row counts, columns, computed-stat keys, web snippet counts, market
series), the previous clarification question if the last turn asked one, and a
digest of the previous answer's data if this looks like a follow-up.

Decide exactly one action:
- "answer" when the evidence can support a response: the query is clear, OR the
  query is a follow-up answerable from prior data, OR enough partial evidence
  exists to answer best-effort with stated assumptions.
- "clarify" ONLY when the evidence cannot support any honest response AND no
  prior clarification asked the same thing. Clarification is a last resort, not
  a default. Asking a second question on a point already asked about is forbidden:
  if PRIOR CLARIFICATION is present and the new query does not resolve it, choose
  "answer" (best-effort with assumptions) instead.

Follow-up intents ("chart that", "show it as bars", "break it down", "why did
that happen") refer to PRIOR DATA: set chart_from_prior true when the query
wants a visual of it.

The visual_plan lists the visuals the evidence supports, using only these kinds:
metric, graph, table, comparison, insight, alert, status. Map by data shape:
time series -> graph/line; few categories + numbers -> graph/bar; headline
number -> metric; two periods -> comparison + table; anomaly -> alert;
freshness/meta -> status; raw rows -> table; cited snippets with no numbers ->
insight. Empty plan ONLY when nothing plottable exists at all.

suggested_options (for clarify only): 2-4 concrete options grounded in the
actually available columns, metrics, or entities - never invented values.

Return ONLY this JSON:
{
  "decision": "answer",
  "missing": "",
  "chart_from_prior": false,
  "visual_plan": [{"kind": "table", "spec": "first rows", "chart_type": null}],
  "suggested_options": []
}
"""


async def judge_sufficiency(
    user_query: str,
    source_scope: str,
    evidence: Dict[str, Any],
    prior_clarification: Optional[str] = None,
    prior_data: Optional[Dict[str, Any]] = None,
) -> Decision:
    """LLM-as-decision-maker: tool call vs clarification vs answer planning.

    Never raises: any failure (transport, bad JSON, schema mismatch) falls
    back to the fail-open "answer" verdict so judging can never block a reply.
    """
    inventory = json.dumps(evidence, indent=2, default=str)
    prior_section = (
        f"PRIOR CLARIFICATION (asked last turn, never restate it):\n"
        f"{prior_clarification or 'none'}"
    )
    prior_data_section = (
        f"PRIOR DATA (previous answer's rows, use for follow-ups):\n"
        f"{json.dumps(prior_data, indent=2, default=str)}"
        if prior_data
        else "PRIOR DATA: none."
    )
    prompt = (
        f"User Query:\n{user_query}\n\n"
        f"Source scope: {source_scope}\n\n"
        f"Evidence inventory (tool outputs on hand):\n{inventory}\n\n"
        f"{prior_section}\n\n"
        f"{prior_data_section}\n\n"
        f"Respond strictly in the decision JSON schema."
    )
    try:
        content: Optional[str] = None
        for attempt in range(2):
            # Live models occasionally return an empty completion on the first
            # try despite HTTP 200; one retry rescues the verdict instead of
            # silently degrading every request to fail-open.
            result = await generate_response(
                prompt=prompt,
                system_prompt=DECISION_SYSTEM_PROMPT,
                temperature=0.0 if attempt == 0 else 0.3,
                max_tokens=400,
            )
            content = (result.get("content") or "").strip()
            if content:
                break
            logger.warning(f"Sufficiency judge got empty content (attempt {attempt + 1}).")
        if not content:
            raise ValueError("empty judge reply after retry")
        return Decision(**normalize_decision_payload(extract_json(content)))
    except Exception as exc:
        logger.warning(f"Sufficiency judge failed, answering best-effort: {exc}")
        return default_decision()


def normalize_decision_payload(payload: Any) -> dict:
    """Coerce a possibly ragged judge reply into Decision-shaped data."""
    if not isinstance(payload, dict):
        return {"decision": "answer"}
    normalized = {
        "decision": payload.get("decision", "answer"),
        "missing": payload.get("missing", "") or "",
        "chart_from_prior": bool(payload.get("chart_from_prior", False)),
        "visual_plan": payload.get("visual_plan", []) or [],
        "suggested_options": payload.get("suggested_options", []) or [],
        "preferred_visual": payload.get("preferred_visual") or None,
    }
    if normalized["decision"] not in ("answer", "clarify"):
        normalized["decision"] = "answer"
    if normalized["preferred_visual"] not in (
        None, "table", "bar", "line", "pie", "area", "metric",
    ):
        normalized["preferred_visual"] = None
    return normalized


def _normalize_text(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


# Explicit output-shape requests ("as a bar chart", "in a table", ...).
# Generic intents only - no topics.
PREFERRED_VISUAL_PATTERNS = (
    (r"\bbar\s*chart\b|\bbars?\b.*\bchart\b|\bas\s*(a\s*)?bars?\b", "bar"),
    (r"\bline\s*chart\b|\bline\s*graph\b|\btrend\s*line\b", "line"),
    (r"\bpie\s*chart\b|\bpie\s*graph\b|\bdonut\b", "pie"),
    (r"\barea\s*chart\b", "area"),
    (r"\b(in|as|into)\s*a\s*table\b|\btabular\b|\btable\s*format\b", "table"),
    (r"\bmetric\b|\bheadline\s*number\b|\bkpi\b", "metric"),
)


def detect_preferred_visual(query: str) -> Optional[str]:
    """Detect an explicitly requested output shape from the query text."""
    lowered = query.lower()
    for pattern, kind in PREFERRED_VISUAL_PATTERNS:
        if re.search(pattern, lowered):
            return kind
    return None


def sanitize_citations(answer: str, source_count: int) -> str:
    """Drop citation markers that point at no listed source ([0], [99] when
    only 3 snippets exist). Valid markers pass through untouched."""
    if source_count <= 0:
        return re.sub(r"\[\d+\]", "", answer)

    def _keep(match: "re.Match[str]") -> str:
        number = int(match.group(1))
        return match.group(0) if 1 <= number <= source_count else ""

    return re.sub(r"\[(\d+)\]", _keep, answer)


def _same_question(first: str, second: str) -> bool:
    """Detect a repeated clarification (the loop that must never happen)."""
    left, right = _normalize_text(first), _normalize_text(second)
    if not left or not right:
        return False
    if left == right:
        return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= 0.82


def build_prompt(
    user_query: str,
    db_data: Sequence[dict],
    computed_numbers: Optional[dict] = None,
    news_context: Optional[list] = None,
    source_scope: SOURCE_SCOPES = "own_data",
    company_name: Optional[str] = None,
    decision: Optional[Decision] = None,
    prior_clarification: Optional[str] = None,
    prior_data: Optional[Dict[str, Any]] = None,
    market_data: Optional[list] = None,
    forbid_clarify: bool = False,
) -> str:
    """Assemble the pipeline prompt (data + precomputed stats + context).

    The model only sees truncated rows + code-computed statistics, so it can
    narrate but never needs to calculate (specs/11 §2). The decision verdict
    and prior-turn context steer it: clarify only on verdict, never repeat a
    prior question, and resolve follow-ups from prior data.
    """
    news_context = news_context or []
    computed_numbers = computed_numbers or {}
    market_data = market_data or []

    rows, truncation_note = _truncate_rows(db_data)
    data_section = "\n".join(json.dumps(rows, indent=2, default=str)) + truncation_note

    if source_scope == "live_web":
        data_section = "Not applicable - this is a live web query; do not discuss user data."

    computed_section = (
        json.dumps(computed_numbers, indent=2, default=str) or "None"
    )

    if news_context:
        # Numbered so the CITATION RULE can point at exact sources ([1], ...).
        news_section = "\n".join(
            f"[{index}] {snippet}"
            for index, snippet in enumerate(news_context, start=1)
        )
    elif source_scope == "own_data":
        news_section = "User asked for their own data only - no live web context."
    else:
        news_section = "User is asking for general knowledge (live web) - use your knowledge to provide direct answers, do not ask for clarifications."

    company_section = company_name or "Not provided"

    market_section = (
        "\n".join(
            f"- {item.get('entity', 'series')}: "
            f"{len(item.get('values', []) or [])} points "
            f"({', '.join(str(label) for label in (item.get('labels', []) or [])[:3])}...)"
            for item in market_data[:4]
        )
        if market_data
        else "none"
    )

    decision_section = (
        f"DECISION VERDICT (follow it): {decision.decision.upper()}\n"
        f"Missing (if clarify): {decision.missing or 'n/a'}\n"
        f"Chart from prior data: {decision.chart_from_prior}\n"
        f"Requested output shape (honor it when the data allows): "
        f"{decision.preferred_visual or 'none'}\n"
        f"Suggested options (if clarify): "
        f"{', '.join(decision.suggested_options) or 'n/a'}\n"
        f"Visual plan (build these from supplied values only): "
        f"{json.dumps([item.model_dump() for item in decision.visual_plan], default=str) or 'none'}"
        if decision is not None
        else "DECISION VERDICT: none (answer best-effort)."
    )

    prior_clarification_section = (
        f"PRIOR CLARIFICATION (asked last turn - NEVER restate it; if the new "
        f"query does not resolve it, answer best-effort with assumptions):\n"
        f"{prior_clarification}"
        if prior_clarification
        else "PRIOR CLARIFICATION: none."
    )

    prior_data_section = (
        f"PRIOR DATA (previous answer's rows - resolve follow-ups from these, "
        f"do not claim the data is missing):\n"
        f"{json.dumps(prior_data, indent=2, default=str)}"
        if prior_data
        else "PRIOR DATA: none."
    )

    forbid_section = (
        "CLARIFICATION IS DISABLED FOR THIS CALL: you MUST return a normal "
        "answer with clarification null. State assumptions in the answer text "
        "instead of asking."
        if forbid_clarify
        else ""
    )

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

{decision_section}

{prior_clarification_section}

{prior_data_section}

Business Data (rows returned by the executed SQL):
{data_section}

Computed Statistics (already calculated by code - narrate these, never re-compute):
{computed_section}

Market Series (verified values, chart these when asked - never invent a series):
{market_section}

Web Search Results:
{news_section}

{forbid_section}

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
        "thinking": [],
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
        "thinking",
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


# Generic chart-intent detection (intents, never topics): follow-ups asking
# for a visual of the current or prior results.
CHART_INTENT_RE = (
    r"\b(chart|graph|plot|visualize|visualise|visual|bar|line|pie|histogram)\b"
    r"|chart form|in a chart|as a chart"
)

_SYNTH_TABLE_MAX_ROWS = 12
_SYNTH_TABLE_MAX_COLS = 6
_SYNTH_SERIES_MAX_POINTS = 30
_SYNTH_SOURCES_MAX_ROWS = 8


def _sources_table_visual(web_sources: list) -> Optional[VisualOutput]:
    """Honest visual for qualitative web answers: the actual cited sources as
    a table (title + provider), so even a prose answer carries an artifact."""
    rows = [
        [str(source.get("title", "Source"))[:80], str(source.get("provider", "Web"))]
        for source in (web_sources or [])
        if source.get("url")
    ][:_SYNTH_SOURCES_MAX_ROWS]
    if not rows:
        return None
    return VisualOutput(
        visual_type="table",
        title="Sources cited",
        props={"columns": ["Source", "Provider"], "values": rows},
    )


def _looks_like_date(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _column_roles(rows: Sequence[dict]) -> Dict[str, Any]:
    """Detect plottable column roles from a row sample (generic, topic-free):
    a date-like column, a low-cardinality text column, and a numeric column."""
    sample = [row for row in list(rows)[:_SYNTH_TABLE_MAX_ROWS] if isinstance(row, dict)]
    if not sample:
        return {}
    columns = list(sample[0].keys())
    roles: Dict[str, Any] = {"columns": columns}
    for column in columns:
        values = [row.get(column) for row in sample]
        present = [value for value in values if value is not None]
        if not present:
            continue
        if all(_is_number(value) for value in present):
            roles.setdefault("numeric", []).append(column)
        elif sum(1 for value in present if _looks_like_date(value)) >= max(
            2, int(0.6 * len(present))
        ):
            # Date-like wins over the low-cardinality-text rule below, so a
            # date axis is never mistreated as bar categories.
            roles.setdefault("date", column)
        elif all(isinstance(value, str) for value in present) and len(set(present)) <= 12:
            roles.setdefault("category", column)
    return roles


def _visuals_from_rows(
    rows: Sequence[dict],
    computed_numbers: Optional[dict],
    preferred_visual: Optional[str] = None,
) -> list:
    """Deterministically synthesize visuals from real tool outputs (rows +
    code-computed stats). Every value comes from the supplied data - nothing
    is invented, so this is always honest. An explicitly requested shape
    (bar/line/pie/area/table/metric) is honored whenever the data supports it;
    otherwise the derivable default leads."""
    computed_numbers = computed_numbers or {}
    sample = list(rows)[:_SYNTH_TABLE_MAX_ROWS]
    columns = list(sample[0].keys())[:_SYNTH_TABLE_MAX_COLS]
    roles = _column_roles(rows)
    numeric = (roles.get("numeric") or [None])[0]
    parts: Dict[str, Any] = {"graph": None, "table": None, "metric": None}
    graph_basis: Optional[str] = None  # "date" | "category" | None

    if roles.get("date") and numeric and len(sample) >= 3:
        date_col = roles["date"]
        labels = [str(row.get(date_col)) for row in sample]
        values = [
            row.get(numeric) if _is_number(row.get(numeric)) else 0 for row in sample
        ]
        parts["graph"] = {
            "visual_type": "graph",
            "title": f"{numeric} over time",
            "props": {
                "chart_type": "line",
                "labels": labels,
                "datasets": [{"name": numeric, "values": values}],
            },
        }
        graph_basis = "date"
    elif roles.get("category") and numeric:
        category_col = roles["category"]
        buckets: Dict[str, float] = {}
        for row in sample:
            key = str(row.get(category_col))
            value = row.get(numeric)
            buckets[key] = buckets.get(key, 0) + (value if _is_number(value) else 0)
        if 2 <= len(buckets) <= 12:
            parts["graph"] = {
                "visual_type": "graph",
                "title": f"{numeric} by {category_col}",
                "props": {
                    "chart_type": "bar",
                    "labels": list(buckets.keys()),
                    "datasets": [{"name": numeric, "values": list(buckets.values())}],
                },
            }
            graph_basis = "category"

    # Honor an explicitly requested chart shape when the derived series allows
    # it (bar/line fit either series; pie needs categories, area needs dates).
    if parts["graph"] is not None and preferred_visual in ("bar", "line", "pie", "area"):
        allowed = {"bar", "line"}
        allowed |= {"pie"} if graph_basis == "category" else set()
        allowed |= {"area"} if graph_basis == "date" else set()
        if preferred_visual in allowed:
            parts["graph"]["props"]["chart_type"] = preferred_visual
            logger.info(f"Visual guarantee honored requested shape: {preferred_visual}.")
        else:
            logger.info(
                f"Requested shape {preferred_visual} not derivable; kept default."
            )

    table = {
        "visual_type": "table",
        "title": f"Results ({len(list(rows))} rows)",
        "props": {
            "columns": columns,
            "values": [[str(row.get(col, "")) for col in columns] for row in sample],
        },
    }
    parts["table"] = table

    totals = computed_numbers.get("totals") or {}
    averages = computed_numbers.get("averages") or {}
    if numeric and (numeric in totals or numeric in averages):
        value = totals.get(numeric, averages.get(numeric))
        metric_props: Dict[str, Any] = {
            "label": f"Total {numeric}" if numeric in totals else f"Average {numeric}",
            "value": value,
            "change_pct": None,
            "direction": "flat",
        }
        growth = computed_numbers.get("growth_pct") or {}
        if growth.get("metric") == numeric and growth.get("last_growth_pct") is not None:
            change = growth["last_growth_pct"]
            metric_props["change_pct"] = change
            metric_props["direction"] = (
                "up" if change > 0 else "down" if change < 0 else "flat"
            )
        parts["metric"] = {
            "visual_type": "metric",
            "props": metric_props,
            "title": f"{numeric}",
        }

    # Requested shape leads when present; otherwise graph, table, metric.
    if preferred_visual == "table":
        order = ["table", "graph", "metric"]
    elif preferred_visual == "metric":
        order = ["metric", "graph", "table"]
    else:
        order = ["graph", "table", "metric"]
    ordered = [parts[kind] for kind in order if parts[kind] is not None]
    # Validated VisualOutput objects (the field holds models, not dicts).
    return [VisualOutput(**visual) for visual in ordered[:3]]


def _market_graph_visual(market_data: list, query: str) -> Optional[VisualOutput]:
    """Generalized market-series graph (replaces the old one-off hardcoded
    stock chart in the route): any entities, downsampled, neutral title."""
    entities = [item for item in market_data if item.get("values")]
    if not entities:
        return None
    entities = entities[:4]
    names = [str(item.get("entity", "series")) for item in entities]
    base_labels = list((entities[0].get("labels") or []))
    stride = max(1, len(base_labels) // _SYNTH_SERIES_MAX_POINTS)
    labels = base_labels[::stride]
    datasets = []
    for item in entities:
        values = list(item.get("values") or [])
        datasets.append({"name": str(item.get("entity", "series")), "values": values[::stride]})
    return VisualOutput(
        visual_type="graph",
        title=f"{', '.join(names)} performance",
        props={"chart_type": "line", "labels": labels, "datasets": datasets},
    )


def ensure_visuals(
    output: PipelineOutput,
    *,
    rows: Sequence[dict],
    computed_numbers: Optional[dict],
    market_data: Optional[list],
    web_sources: Optional[list],
    preferred_visual: Optional[str],
    query: str,
) -> PipelineOutput:
    """Visual guarantee: a validated normal answer must never go out naked
    when plottable tool outputs exist. Clarifications and already-visual
    answers pass through untouched; synthesis only uses real values. A
    qualitative web answer with cited sources still gets a sources table."""
    if output.clarification is not None or output.visuals:
        return output
    rows = list(rows or [])
    market_data = list(market_data or [])
    web_sources = list(web_sources or [])
    synthesized: list = []
    if rows:
        chart_requested = bool(re.search(CHART_INTENT_RE, query, re.IGNORECASE))
        synthesized = _visuals_from_rows(rows, computed_numbers, preferred_visual)
        if chart_requested:
            logger.info("Chart intent detected; synthesized visuals lead with a chart.")
    elif market_data:
        graph = _market_graph_visual(market_data, query)
        if graph is not None:
            synthesized = [graph]
    elif web_sources:
        table = _sources_table_visual(web_sources)
        if table is not None:
            synthesized = [table]
    if synthesized:
        logger.info(f"Visual guarantee synthesized {len(synthesized)} visual(s).")
        output.visuals = list(output.visuals) + synthesized[:3]
    return output


async def run_pipeline(
    user_query: str,
    db_data: Sequence[dict],
    computed_numbers: Optional[dict] = None,
    news_context: Optional[list] = None,
    source_scope: SOURCE_SCOPES = "own_data",
    company_name: Optional[str] = None,
    prior_clarification: Optional[str] = None,
    prior_data: Optional[Dict[str, Any]] = None,
    market_data: Optional[list] = None,
    web_sources: Optional[list] = None,
) -> PipelineOutput:
    """Decision-driven pipeline: judge -> narrate -> guarantee (specs/06).

    1. The sufficiency judge (LLM) decides answer-vs-clarify and plans visuals
       from the actual tool outputs on hand.
    2. The narration call answers following that verdict, with prior-turn
       context so follow-ups resolve instead of looping.
    3. The deterministic visual guarantee fills visuals from real rows/series
       when a validated answer arrives naked.
    4. A repeat-clarification backstop re-asks the narrator once with
       clarification disabled, so the user never gets the same question twice.

    Deterministic numbers are always precomputed by the caller and passed in;
    this function only narrates them. Falls back to a low-confidence generic
    PipelineOutput (never raises) on any malformed/validation failure
    (specs/06 FR4).
    """
    if news_context is None:
        news_context = []
    if computed_numbers is None:
        computed_numbers = {}
    if market_data is None:
        market_data = []
    if web_sources is None:
        web_sources = []

    rows = list(db_data or [])
    thinking: List[str] = []

    try:
        decision = await judge_sufficiency(
            user_query=user_query,
            source_scope=source_scope,
            evidence=_evidence_inventory(
                rows, computed_numbers, news_context, market_data
            ),
            prior_clarification=prior_clarification,
            prior_data=prior_data,
        )
        # An explicitly requested shape in the query wins over the judge's.
        detected = detect_preferred_visual(user_query)
        if detected is not None:
            decision.preferred_visual = detected
        logger.info(
            f"Pipeline decision: {decision.decision} "
            f"(chart_from_prior={decision.chart_from_prior}, "
            f"preferred={decision.preferred_visual}, "
            f"plan={[item.kind for item in decision.visual_plan]})"
        )
        thinking.append(
            f"Judged: {decision.decision} "
            f"({len(rows)} row(s), {len(news_context)} snippet(s), "
            f"{len(market_data)} market series)"
        )

        narrate_rows = rows
        if decision.chart_from_prior and prior_data and not rows:
            # Follow-up on the previous answer ("chart that"): narrate from
            # the prior rows so the request resolves instead of clarifying.
            narrate_rows = prior_data.get("rows", []) or []
        if not narrate_rows and prior_data:
            # Deterministic backstop (no judge needed): an explicit chart
            # request with no fresh rows but a prior answer's rows always
            # resolves from the prior rows.
            if re.search(CHART_INTENT_RE, user_query, re.IGNORECASE):
                logger.info("Chart follow-up resolved from prior answer rows.")
                narrate_rows = prior_data.get("rows", []) or []

        output = await _narrate(
            user_query=user_query,
            db_data=narrate_rows,
            computed_numbers=computed_numbers,
            news_context=news_context,
            source_scope=source_scope,
            company_name=company_name,
            decision=decision,
            prior_clarification=prior_clarification,
            prior_data=prior_data,
            market_data=market_data,
            forbid_clarify=False,
        )

        if (
            output.clarification is not None
            and prior_clarification
            and _same_question(
                output.clarification.question, prior_clarification
            )
        ):
            # Anti-loop backstop: the same question twice is forbidden, so
            # answer best-effort with assumptions instead of re-asking.
            logger.warning("Repeat clarification blocked; answering best-effort.")
            thinking.append("Repeat question blocked; answered best-effort.")
            output = await _narrate(
                user_query=user_query,
                db_data=narrate_rows,
                computed_numbers=computed_numbers,
                news_context=news_context,
                source_scope=source_scope,
                company_name=company_name,
                decision=decision,
                prior_clarification=prior_clarification,
                prior_data=prior_data,
                market_data=market_data,
                forbid_clarify=True,
            )

        if output.clarification is None and news_context:
            # Strip citation markers that point at no listed source.
            output.answer = sanitize_citations(output.answer, len(news_context))

        if output.clarification is not None and not output.clarification.options:
            # Both affordances, always: pills to tap AND a box to type. When
            # the narrator leaves options empty, backfill from the judge's
            # evidence-grounded suggestions; genuinely open-ended questions
            # keep [] and render type-only.
            if decision.suggested_options:
                output.clarification.options = list(decision.suggested_options[:4])
                logger.info("Backfilled clarification options from judge suggestions.")

        if output.clarification is not None:
            thinking.append(
                f"Clarifying (one question): {output.clarification.question[:120]}"
            )
        else:
            plan = [item.kind for item in decision.visual_plan]
            thinking.append(
                f"Answered from {len(narrate_rows)} row(s) + "
                f"{len(news_context)} snippet(s)"
                + (f"; planned visuals: {', '.join(plan)}" if plan else "")
            )

        output = ensure_visuals(
            output,
            rows=narrate_rows,
            computed_numbers=computed_numbers,
            market_data=market_data,
            web_sources=web_sources,
            preferred_visual=decision.preferred_visual,
            query=user_query,
        )
        if output.visuals:
            thinking.append(
                f"Visuals out: {', '.join(v.visual_type for v in output.visuals)}"
            )
        output.thinking = thinking + list(output.thinking or [])
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


async def _narrate(
    user_query: str,
    db_data: Sequence[dict],
    computed_numbers: dict,
    news_context: list,
    source_scope: SOURCE_SCOPES,
    company_name: Optional[str],
    decision: Decision,
    prior_clarification: Optional[str],
    prior_data: Optional[Dict[str, Any]],
    market_data: list,
    forbid_clarify: bool,
) -> PipelineOutput:
    """One narration call: prompt -> LLM -> validated PipelineOutput."""
    prompt = build_prompt(
        user_query,
        db_data,
        computed_numbers,
        news_context,
        source_scope,
        company_name,
        decision=decision,
        prior_clarification=prior_clarification,
        prior_data=prior_data,
        market_data=market_data,
        forbid_clarify=forbid_clarify,
    )

    result = await generate_response(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=2000,
    )

    raw_output = (result.get("content") or "").strip()
    if not raw_output:
        # Same transient-empty-completion rescue as the judge: one retry with
        # slightly higher temperature before giving up on this narration.
        logger.warning("Narration got empty content; retrying once.")
        result = await generate_response(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=2000,
        )
        raw_output = (result.get("content") or "").strip()
    logger.info(f"LLM source used: {result.get('source', 'unknown')}")

    parsed = normalize_pipeline_payload(extract_json(raw_output))

    return PipelineOutput(**parsed)


def _evidence_inventory(
    rows: Sequence[dict],
    computed_numbers: dict,
    news_context: list,
    market_data: list,
) -> Dict[str, Any]:
    """Describe what the tools actually returned, for the judge's verdict."""
    rows = list(rows or [])
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    return {
        "row_count": len(rows),
        "columns": columns,
        "computed_stat_keys": sorted((computed_numbers or {}).keys()),
        "web_snippet_count": len(news_context or []),
        "market_entities": [
            str(item.get("entity", "series")) for item in (market_data or [])
        ],
    }