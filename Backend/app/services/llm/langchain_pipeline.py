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
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.services.llm.groq_service import generate_response
from app.config import get_settings

try:
    from app.services.data.comparison import (
        ComparisonEvidence,
        METRIC_PROFIT,
        METRIC_REVENUE,
        METRIC_STOCK,
        build_research_plan,
        build_trace,
        check_entity_completeness,
        check_research_completeness,
        clarification_asks_for_researchable_data,
        comparison_confidence,
        compute_comparison_stats,
        compute_net_margins,
        compute_pct_change,
        compute_yearly_stats,
        decompose_comparison_query,
        evidence_currency,
        evidence_driven_confidence,
        figures_share_metric,
        figure_entity_label,
        figure_metric_label,
        format_runtime_trace,
        insufficient_reason,
        is_comparison_query,
        is_figure_comparison_eligible,
        is_researchable_comparison,
        must_not_clarify,
        normalize_currency,
        parse_time_range,
        reconcile_judge_tools,
        required_tools_for_query,
        resolve_tool_plan,
        validate_calculation_inputs,
        validate_comparison,
        validate_historical_coverage,
    )
except Exception:  # pragma: no cover - import-time safety, never blocks pipeline
    ComparisonEvidence = None  # type: ignore
    METRIC_STOCK = "stock_performance"
    METRIC_REVENUE = "revenue_growth"
    METRIC_PROFIT = "profitability"
    build_research_plan = None  # type: ignore
    clarification_asks_for_researchable_data = None  # type: ignore
    comparison_confidence = None  # type: ignore
    compute_comparison_stats = None  # type: ignore
    compute_net_margins = None  # type: ignore
    compute_pct_change = None  # type: ignore
    compute_yearly_stats = None  # type: ignore
    decompose_comparison_query = None  # type: ignore
    evidence_currency = None  # type: ignore
    evidence_driven_confidence = None  # type: ignore
    figures_share_metric = None  # type: ignore
    figure_entity_label = None  # type: ignore
    figure_metric_label = None  # type: ignore
    insufficient_reason = None  # type: ignore
    is_comparison_query = None  # type: ignore
    must_not_clarify = None  # type: ignore
    normalize_currency = None  # type: ignore
    parse_time_range = None  # type: ignore
    build_trace = None  # type: ignore
    check_entity_completeness = None  # type: ignore
    check_research_completeness = None  # type: ignore
    format_runtime_trace = None  # type: ignore
    is_figure_comparison_eligible = None  # type: ignore
    reconcile_judge_tools = None  # type: ignore
    required_tools_for_query = None  # type: ignore
    resolve_tool_plan = None  # type: ignore
    validate_calculation_inputs = None  # type: ignore
    validate_comparison = None  # type: ignore
    validate_historical_coverage = None  # type: ignore

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
- WHAT-IF RULE: when Computed Statistics contains a what_if scenario, quote its
  baseline_total / scenario_total / delta exactly and state its assumption
  string verbatim alongside the result (specs/11 §3.3). Never model elasticity
  or adjust the numbers yourself.
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
    NEVER ask the user to supply public company statistics (annual revenue,
    net income, profit margins, stock prices, financial figures). When the
    query names public companies plus the metrics and period wanted, that
    data is researchable: answer from the supplied evidence and state what
    is missing instead of asking the user to provide figures.
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
- VISUAL MANDATE: every normal answer MUST include visuals whenever anything
    plottable exists (evidence rows, market series, or prior data) - and more than
    one whenever the evidence supports more than one shape (chart AND table,
    table AND metric, graph AND sources table). One lonely visual is a failure
    when two honest ones fit. Choose
    by data shape, not by topic:
      a date-like column + a numeric column (3+ rows) -> line or area graph;
      a text column with 2-12 distinct values + a numeric column -> bar graph;
      one headline number -> metric card (with change/direction when growth stats exist);
      two periods of the same metric -> comparison card AND a table;
      a threshold breach or anomaly -> alert card;
      data freshness or sync metadata -> status card;
      otherwise, or additionally -> a table card of the real rows.
    All visual values MUST come from the supplied sections - never invent numbers to
    fill a chart. An empty visuals list is allowed ONLY for (a) a purely qualitative
    answer with no plottable evidence at all (then include a comparison or table
    card organizing the cited options/entities, rows from the snippets, never
    invented, AND at least one insight card grounded in the cited snippets) or
    (b) a BLOCKED historical comparison (insufficient multi-year evidence):
    then return NO chart at all and state the missing data instead.
- CITATION RULE: Web Search Results are numbered ([1], [2], ...). Every factual
    claim taken from them MUST carry its source number inline, e.g. "raised $50M
    in 2024 [2]". Cite the exact snippet each fact came from; never cite a number
    that was not listed, and never invent sources. Claims from your own general
    knowledge need no marker - but prefer snippet-grounded claims whenever the
    snippets cover the point. When a snippet carries a date ([n, YYYY-MM-DD]),
    respect it: never present an old figure as current, and say when dates differ.
- COMPARISON RULE: for explicit "X vs Y" / "compare" questions, prefer a
    comparison visual (groups for each side, values from the snippets only)
    over a generic bar chart, and never invent a missing side's number.
    Only figures whose meaning is bound (entity AND metric known) may enter
    a comparison visual; untyped figures stay cited prose, never chart data.
- HISTORICAL COMPARISON RULE: when a Comparison Gate section is present,
  OBEY it. BLOCKED means: state what historical data is missing for ALL
  compared companies (which metric, which window, which companies), set
  confidence 0.0, return NO graph/comparison visual (empty visuals list is
  correct here), and never declare winners. PASSED means: quote
  comparison_stats exactly (start/end/pct_change per company per metric,
  year-by-year values, net margins, winners, formula, assumptions), name a
  winner for EACH requested metric (stock performance, revenue growth,
  profitability -- profitability means highest latest net profit margin =
  net income / revenue * 100), give the actual statistics, add a concise
  hedged explanation of the key reasons with [n] citations, and keep
  confidence > 0.
  A one-month series or a current snapshot NEVER covers "last N years":
  never present one as if it did, and never compare current vs historical,
  funding vs cost, or monthly vs multi-year figures as like-for-like.
- OUTLOOK RULE: for sentiment/opinion/outlook questions with no hard numbers,
    use a status badge grounded in the cited snippets (state what the sources
    suggest, not your own verdict) plus a sources table - never a fabricated chart.
- PRIOR RESULT RULE: when PRIOR DATA is present and the query is a follow-up on it
    (chart it, filter it, compare it, explain a part of it), answer from PRIOR DATA.
    Do not claim the data is missing and do not re-ask what was already answered.
- CLARIFICATION DISCIPLINE: clarify only when the DECISION section says so. Ask
    exactly the decided question, never the PRIOR CLARIFICATION question restated,
    and ground every option in actually available evidence (columns, metrics, or
    the decided suggestions). A second clarification on the same point is forbidden:
    answer best-effort and state your assumptions instead.
- FOLLOW-UPS: for a normal answer, end with 2-3 short follow-up questions the user
    would plausibly ask next about THIS answer (a drill-down, a comparison, a
    different cut). Each must be answerable from the evidence at hand or a trivial
    follow-up query - never a repeat of the current question, never a question the
    answer already resolves. Omit ([]) for clarifications and fallbacks.

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
  "clarification": null,
  "followups": ["...", "..."]
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
- NEVER "clarify" by asking the user to provide public company statistics
  (annual revenue, net income, profit margins, stock prices). When the query
  already names the companies, the metrics, and the period, the research
  stage owns data collection: choose "answer" and let the response state
  which evidence could not be obtained.

Follow-up intents ("chart that", "show it as bars", "break it down", "why did
that happen") refer to PRIOR DATA: set chart_from_prior true when the query
wants a visual of it.

The visual_plan lists the visuals the evidence supports, using only these kinds:
metric, graph, table, comparison, insight, alert, status. Map by data shape:
time series -> graph/line; few categories + numbers -> graph/bar; headline
number -> metric; two periods -> comparison + table; anomaly -> alert;
freshness/meta -> status; raw rows -> table; cited snippets with no numbers ->
insight. Empty plan ONLY when nothing plottable exists at all.
Steer explicitly: "X vs Y"/compare questions -> comparison; dated news
snippets ("what's going on with X") -> table timeline; sentiment/outlook
questions with no numbers -> status outlook badge + sources table.

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
            # try despite HTTP 200, and long option lists can truncate the JSON
            # mid-string at a tight token budget (seen live: suggested_options
            # cut off, failing extraction and silently degrading to fail-open).
            # One retry with more headroom rescues the verdict instead.
            result = await generate_response(
                prompt=prompt,
                system_prompt=DECISION_SYSTEM_PROMPT,
                model=get_settings().groq_fast_model,
                temperature=0.0 if attempt == 0 else 0.3,
                max_tokens=800,
                json_mode=True,
            )
            content = (result.get("content") or "").strip()
            if not content:
                logger.warning(f"Sufficiency judge got empty content (attempt {attempt + 1}).")
                continue
            try:
                return Decision(**normalize_decision_payload(extract_json(content)))
            except Exception as parse_exc:
                logger.warning(
                    f"Sufficiency judge reply unparseable (attempt {attempt + 1}): {parse_exc}"
                )
                content = None
                continue
        if not content:
            raise ValueError("empty or unparseable judge reply after retry")
    except Exception as exc:
        logger.warning(f"Sufficiency judge failed, answering best-effort: {exc}")
        return default_decision()


# Tool catalog for judge-directed routing (see `plan_tools`). Name ->
# one-line description. The planner returns `tools_needed` from these keys;
# dispatch (`search_web`) runs exactly the planned adapters, falling back to
# the deterministic predicates when the plan is empty or invalid.
#
# Historical honesty: "market" is a ONE-MONTH series (short-term only) and
# "fundamentals" is a CURRENT snapshot (no dates). Neither can answer a
# multi-year question. "market_history" (multi-year weekly closes) and
# "financial_history" (annual revenue + net-income series) are the ONLY
# adapters that satisfy "last N years" stock / revenue / profitability asks.
TOOL_CATALOG: Dict[str, str] = {
    "snippets": "fresh web search snippets (general facts, news, opinions)",
    "market": "one-month Yahoo price series for a named company/entity (short-term only, never historical)",
    "fundamentals": "Yahoo company fundamentals snapshot: market cap, P/E, revenue (current only, no history)",
    "market_history": "multi-year Yahoo weekly price history for a named company (use for 'last N years' stock performance)",
    "financial_history": "annual Yahoo revenue + net-income history for a named company (use for multi-year revenue growth / profitability)",
    "wikipedia": "canonical Wikipedia summary for a named entity (who/what is X)",
    "macro": "FRED macro series: inflation/CPI, unemployment, rates, GDP",
    "extract": "full clean content of a URL mentioned verbatim in the query",
}


def normalize_tool_plan(value: Any) -> List[str]:
    """Coerce a ragged tools_needed value into catalog keys, order-kept."""
    if not isinstance(value, list):
        return []
    planned: List[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name in TOOL_CATALOG and name not in planned:
            planned.append(name)
    return planned


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
        "tools_needed": normalize_tool_plan(payload.get("tools_needed", [])),
    }
    if normalized["decision"] not in ("answer", "clarify"):
        normalized["decision"] = "answer"
    if normalized["preferred_visual"] not in (
        None, "table", "bar", "line", "pie", "area", "metric",
    ):
        normalized["preferred_visual"] = None
    return normalized


PLAN_SYSTEM_PROMPT = """You route a live-web question to evidence tools. You do NOT answer
the user. Pick the MINIMAL set of tools that could answer the question -
every tool costs latency, so omit anything the question does not need.

Return ONLY this JSON: {"tools_needed": ["snippets", ...]}

Tool catalog (keys and nothing else):
__CATALOG__

Rules (all generic, no topic special-casing):
- "snippets" for almost every live-web question: fresh facts, news,
  opinions, fast-moving figures, and the explanatory "why" behind a
  difference. Multi-metric historical comparisons ALWAYS need "snippets"
  for the reasons/explanation even when structured tools cover the numbers.
- "market" ONLY for short-term price asks (current price, this week/month).
  NEVER use "market" alone for "last N years" / multi-year performance:
  it is a one-month series and cannot satisfy history.
- "market_history" when the question wants stock/price performance over
  years ("last 3 years", "3-year", "trailing N years", multi-year trend).
- "fundamentals" for CURRENT company scale/valuation (market cap, P/E,
  current revenue). It is a snapshot with no dates: NEVER list it as the
  tool for historical revenue GROWTH or multi-year profitability.
- "financial_history" when the question wants revenue growth or
  profitability over years (annual revenue / net-income history).
- A multi-entity, multi-metric historical comparison (e.g. stock AND
  revenue AND profitability over years for two or more companies) needs
  ["market_history", "financial_history", "fundamentals", "snippets"]
  (fundamentals for current scale context, snippets for the explanation).
  Request history tools for EVERY named company, never just the first two.
- "wikipedia" for "who/what is X" grounding (people, companies, industries,
  general concepts) - canonical structured facts, not search snippets.
- "macro" for economy-wide indicators (inflation/CPI, unemployment, interest
  rates, GDP, recession). Omit when the question is about one company only.
- "extract" ONLY when the query pastes a URL to read in full. Never invent
  a URL: no URL in the message means no "extract".
- When a capability is unavailable, do NOT plan around it - list the tools
  the question needs regardless; dispatch skips unavailable ones itself.
- Unknowns, empty tools, or non-catalog names are dropped downstream, so
  when in doubt return ["snippets"] rather than nothing."""


async def plan_tools(
    user_query: str,
    *,
    source_scope: str = "live_web",
    company_name: Optional[str] = None,
    prior_clarification: Optional[str] = None,
    has_tavily_key: bool = False,
    has_fred_key: bool = False,
) -> List[str]:
    """Judge-directed tool routing: which evidence adapters a live-web
    question needs, from the tool catalog. Never raises: any failure
    (transport, bad JSON, schema mismatch) returns [] meaning "no opinion",
    and dispatch falls back to the deterministic predicates - so a planner
    outage can never narrow or block an answer."""
    catalog_lines = "\n".join(
        f"- {name}: {description}" for name, description in TOOL_CATALOG.items()
    )
    system_prompt = PLAN_SYSTEM_PROMPT.replace("__CATALOG__", catalog_lines)
    context_lines = [f"Source scope: {source_scope}"]
    if company_name:
        context_lines.append(f"Company context: {company_name}")
    if prior_clarification:
        context_lines.append(f"Previously asked: {prior_clarification}")
    capabilities = (
        f"Capabilities: Tavily search/extract "
        f"{'available' if has_tavily_key else 'unavailable'}, "
        f"FRED macro series "
        f"{'available' if has_fred_key else 'unavailable'}."
    )
    prompt = (
        f"User Query:\n{user_query}\n\n"
        + "\n".join(context_lines)
        + f"\n{capabilities}\n\nRespond strictly in the routing JSON schema."
    )
    try:
        content: Optional[str] = None
        for attempt in range(2):
            result = await generate_response(
                prompt=prompt,
                system_prompt=system_prompt,
                model=get_settings().groq_fast_model,
                temperature=0.0 if attempt == 0 else 0.3,
                max_tokens=200,
                json_mode=True,
            )
            content = (result.get("content") or "").strip()
            if content:
                break
            logger.warning(f"Tool planner got empty content (attempt {attempt + 1}).")
        if not content:
            return []
        return normalize_tool_plan(extract_json(content).get("tools_needed", []))
    except Exception as exc:
        logger.warning(f"Tool planner failed, using default dispatch: {exc}")
        return []


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
    only 3 snippets exist). Valid markers pass through untouched. Year
    brackets ([2024]) are never citations and are always preserved."""
    try:
        from app.services.data.canonical import sanitize_citations_safe

        return sanitize_citations_safe(answer, source_count)
    except Exception:
        pass
    if source_count <= 0:
        # Preserve 4-digit years even with zero sources.
        return re.sub(r"\[(?!(?:19|20)\d{2}\])\d+\]", "", answer)

    def _keep(match: "re.Match[str]") -> str:
        raw = match.group(1)
        if len(raw) >= 4:
            return match.group(0)
        number = int(raw)
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
    web_sources: Optional[list] = None,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    macro_note: Optional[str] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    comparison_gate: Optional[Dict[str, Any]] = None,
) -> str:
    """Assemble the pipeline prompt (data + precomputed stats + context).

    The model only sees truncated rows + code-computed statistics, so it can
    narrate but never needs to calculate (specs/11 §2). The decision verdict
    and prior-turn context steer it: clarify only on verdict, never repeat a
    prior question, and resolve follow-ups from prior data. Web snippets are
    numbered with per-snippet dates ([n, YYYY-MM-DD]) when the source carries
    one, so the model can tell last week from three years ago.
    """
    news_context = news_context or []
    computed_numbers = computed_numbers or {}
    market_data = market_data or []
    web_sources = web_sources or []
    fundamentals = fundamentals or []
    macro_data = macro_data or []
    macro_note = macro_note or ""
    price_history = price_history or []
    financial_history = financial_history or []
    comparison_gate = comparison_gate or {}

    rows, truncation_note = _truncate_rows(db_data)
    data_section = "\n".join(json.dumps(rows, indent=2, default=str)) + truncation_note

    if source_scope == "live_web":
        data_section = "Not applicable - this is a live web query; do not discuss user data."

    computed_section = (
        json.dumps(computed_numbers, indent=2, default=str) or "None"
    )

    if news_context:
        # Numbered so the CITATION RULE can point at exact sources ([1], ...),
        # with per-snippet dates ([n, YYYY-MM-DD]) when the source carries one.
        dated_lines = []
        for index, snippet in enumerate(news_context, start=1):
            date = ""
            if index - 1 < len(web_sources):
                raw_date = (web_sources[index - 1] or {}).get("published_date")
                if raw_date:
                    date = f", {str(raw_date)[:10]}"
            dated_lines.append(f"[{index}{date}] {snippet}")
        news_section = "\n".join(dated_lines)
    elif source_scope == "own_data":
        news_section = "User asked for their own data only - no live web context."
    else:
        # Fail-closed no-evidence mode (P1#26): never instruct the model to
        # use its own knowledge for statistics. Without web evidence the
        # honest response is an insufficiency report, not hallucinated data.
        news_section = (
            "No usable live-web evidence was retrieved for this query. "
            "Do NOT use your own knowledge for figures, statistics, or "
            "comparisons. State clearly that evidence is insufficient, "
            "describe what is missing, set confidence 0.0, and return NO "
            "numeric graph/comparison visual."
        )

    company_section = company_name or "Not provided"

    # Separate channels (P0#15): market_data and macro_data are NEVER merged.
    # A market chart must never contain CPI/GDP/unemployment series.
    market_section = (
        "\n".join(
            f"- {item.get('entity', 'series')}: "
            f"{len(item.get('values', []) or [])} points "
            f"({', '.join(str(label) for label in (item.get('labels', []) or [])[:3])}...)"
            for item in list(market_data)[:6]
        )
        if market_data
        else "none"
    )
    macro_section = (
        "\n".join(
            f"- {item.get('entity', 'series')}: "
            f"{len(item.get('values', []) or [])} points "
            f"({', '.join(str(label) for label in (item.get('labels', []) or [])[:3])}...)"
            for item in list(macro_data)[:6]
        )
        if macro_data
        else "none"
    )
    if macro_note:
        # Honest-gap disclosure (P4): the narrator must state the
        # structural coverage gap plainly instead of implying no evidence
        # exists anywhere.
        macro_section += (
            "\nData-gap notice (state this honestly in the answer; it is "
            f"status, not evidence): {macro_note}"
        )

    fundamentals_section = (
        "\n".join(
            f"- {item.get('entity', '')} ({item.get('symbol', '')}): "
            + ", ".join(
                f"{key}={item.get(key)}"
                for key in ("market_cap", "pe_ratio", "revenue")
                if item.get(key) is not None
            )
            + " [CURRENT SNAPSHOT -- no dates, never use for historical growth]"
            for item in fundamentals[:4]
        )
        if fundamentals
        else "none"
    )

    history_section = "none"
    if price_history or financial_history:
        lines: list[str] = []
        for item in list(price_history)[:4]:
            labels = list(item.get("labels", []) or [])
            values = list(item.get("values", []) or [])
            lines.append(
                f"- {item.get('entity', '')} ({item.get('symbol', '')}) "
                f"price history: {len(values)} weekly points "
                f"{str(labels[0])[:10] if labels else '?'} -> "
                f"{str(labels[-1])[:10] if labels else '?'} "
                f"(range={item.get('range', '')}, freq={item.get('frequency', '')})"
            )
        for item in list(financial_history)[:4]:
            for block_key in ("revenue", "net_income"):
                block = (item or {}).get(block_key, {}) or {}
                labels = list(block.get("labels", []) or [])
                values = list(block.get("values", []) or [])
                if not values:
                    continue
                lines.append(
                    f"- {item.get('entity', '')} ({item.get('symbol', '')}) "
                    f"{block_key}: {len(values)} annual points "
                    f"{str(labels[0])[:10] if labels else '?'} -> "
                    f"{str(labels[-1])[:10] if labels else '?'} "
                    f"(metric={block.get('metric', '')})"
                )
        history_section = "\n".join(lines) if lines else "none"
    elif market_data:
        # Short-term series present but no multi-year history: say so
        # explicitly so the model never mistakes 1 month for 3 years.
        history_section = (
            "none -- only a short-term (one-month) price series is available; "
            "it does NOT cover a multi-year request."
        )

    gate_section = "No historical comparison gate evaluated."
    validated_section = "None -- no deterministically validated comparison evidence."
    if comparison_gate.get("applies"):
        if comparison_gate.get("blocked"):
            gate_section = (
                "HISTORICAL COMPARISON GATE: BLOCKED -- evidence insufficient. "
                f"{comparison_gate.get('blocked_reason', '')} "
                "Do NOT present a comparison chart or winners. State what data "
                "is missing, keep confidence at 0, and do not invent figures."
            )
            validated_section = (
                "None -- the comparison gate BLOCKED, so NOTHING below counts "
                "as validated comparison evidence. Every number in Web Search "
                "Results / Fundamentals / Market Series is UNVALIDATED CONTEXT."
            )
        else:
            gate_section = (
                "HISTORICAL COMPARISON GATE: PASSED -- validated multi-year "
                f"evidence for {comparison_gate.get('entities', [])} over "
                f"{comparison_gate.get('years')}Y. Quote the Computed "
                "Statistics comparison_stats exactly (winners + pct changes + "
                "formula/assumptions) and cite sources for explanatory claims."
            )
            validated_section = (
                "The Computed Statistics comparison_stats block below. ONLY "
                "this block may support numerical claims, winners, and "
                "charts. Everything else (snippets, snapshots, series) is "
                "UNVALIDATED CONTEXT: usable for hedged qualitative reasons "
                "with [n] citations, never for quantitative comparisons."
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

VALIDATED EVIDENCE (only this may support numerical claims, winners, charts):
{validated_section}

Comparison Gate (must obey: BLOCKED means no chart, no winners, state missing data):
{gate_section}

Market Series (SHORT-TERM one-month values only - NEVER use for "last N years"):
{market_section}

Macro Series (economy-wide FRED indicators - NEVER chart as company market data):
{macro_section}

Price/Financial History (multi-year validated evidence - use for historical asks):
{history_section}

Fundamentals (CURRENT snapshot values - quote these, never re-derive, never use for historical growth):
{fundamentals_section}

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
    """Robust JSON extraction: whole/fenced parse, balanced-brace scan with
    string awareness (prose braces never confuse it), legacy fallback."""
    try:
        from app.services.data.canonical import extract_json_robust

        return extract_json_robust(text)
    except ValueError:
        raise
    except Exception:
        pass
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
        "followups": [],
        "research_state": None,
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
    followups = normalized.get("followups")
    normalized["followups"] = (
        [str(item).strip() for item in followups if str(item).strip()][:3]
        if isinstance(followups, list)
        else []
    )
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

# Explicit "X vs Y" comparison intent: steers web-only answers toward a
# comparison visual (nothing steered there before).
COMPARISON_INTENT_RE = re.compile(
    r"\b(vs\.?|versus|compare|comparison|contrast)\b|\b\w+\s+or\s+\w+",
    re.IGNORECASE,
)

# Sentiment/outlook intent: no hard numbers expected, so a citation-backed
# status badge beats a fabricated chart.
SENTIMENT_INTENT_RE = re.compile(
    r"\b(sentiment|outlook|opinion|bullish|bearish|optimistic|pessimistic|"
    r"prospects?|future|should\s+i\s+(buy|sell|invest)|good\s+time|"
    r"what(?:'s| is) going on|latest\s+news)\b",
    re.IGNORECASE,
)

_POSITIVE_WORDS = {
    "growth", "growths", "gain", "gains", "gained", "rise", "rises", "rising",
    "rose", "record", "beat", "beats", "strong", "bullish", "optimistic",
    "upgrade", "upgrades", "profit", "profits", "surge", "surged", "high",
}
_NEGATIVE_WORDS = {
    "fall", "falls", "fell", "drop", "drops", "dropped", "decline", "declines",
    "declined", "loss", "losses", "lost", "weak", "bearish", "pessimistic",
    "downgrade", "downgrades", "miss", "missed", "low", "crash", "crashed",
    "layoff", "layoffs", "fraud", "lawsuit",
}

_SYNTH_TABLE_MAX_ROWS = 12
_SYNTH_TABLE_MAX_COLS = 6
_SYNTH_SERIES_MAX_POINTS = 30
_SYNTH_SOURCES_MAX_ROWS = 8
_SYNTH_FIGURES_MAX = 8
# Financial tables must never silently drop a whole company to a row cap
# (the live Toyota omission): budget holds 4 companies x 6 annual points.
_SYNTH_FINANCIAL_MAX_ROWS = 24


def _interleave_entities(rows: list[list], max_rows: int) -> list[list]:
    """Round-robin interleave table rows by entity (first column) so a row
    cap degrades to fewer years per company, never to a missing company.
    Deterministic: entities alphabetical, rows stable within entity."""
    by_entity: Dict[str, list] = {}
    for row in rows:
        by_entity.setdefault(str(row[0]) if row else "", []).append(row)
    ordered_entities = sorted(by_entity)
    out: list[list] = []
    index = 0
    while len(out) < max_rows:
        progressed = False
        for entity in ordered_entities:
            bucket = by_entity[entity]
            if index < len(bucket):
                out.append(bucket[index])
                progressed = True
                if len(out) >= max_rows:
                    break
        if not progressed:
            break
        index += 1
    return out

# Verbatim figures with explicit units only (money or percent). Bare numbers
# ("30 ideas", "8 months", years like "2024") never qualify, so dates and
# counts cannot leak into charts.
_FIGURE_MONEY_RE = re.compile(
    r"([$€₹£])\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|B|million|billion|thousand)?"
)
_FIGURE_PERCENT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(%|percent)")
_FIGURE_SCALE = {
    "k": 1e3, "K": 1e3, "thousand": 1e3,
    "M": 1e6, "million": 1e6,
    "B": 1e9, "billion": 1e9,
}


def _figures_from_snippets(
    snippets: Optional[list], query: Optional[str] = None
) -> list:
    """Extract cited money/percent figures verbatim from web snippets.

    Each figure keeps its exact text, a normalized value for bar heights, a
    unit class (money vs percent, never mixed on one chart), the FULL
    snippet scope for semantic binding (H3: never truncated before
    WHO/WHAT/WHEN/UNIT/CURRENCY/DEFINITION resolution), the snippet it
    came from, and the citation number. Semantic binding (entity / metric
    / period / currency / frequency / definition / scale) is attached
    deterministically against the query's entities when `query` is given;
    a figure is comparison_eligible ONLY under the strict H2 contract
    (entity + specific metric + explicit ISO currency for money + ...).
    Untyped "$202B" (no known meaning) stays citable in the figures table
    but must never enter numerical comparison. No NLP, no invention.
    """
    figures: list = []
    seen: set[str] = set()
    queried_entities: list = []
    try:
        if decompose_comparison_query is not None and query:
            queried_entities = (
                decompose_comparison_query(query) or {}
            ).get("entities", []) or []
    except Exception:
        queried_entities = []
    for index, snippet in enumerate(snippets or [], start=1):
        text = str(snippet)
        for match in _FIGURE_MONEY_RE.finditer(text):
            amount = float(match.group(2).replace(",", ""))
            scale = _FIGURE_SCALE.get(match.group(3) or "", 1)
            figures.append(
                _bind_figure(
                    text=text,
                    match_text=match.group(0).strip(),
                    value=amount * scale,
                    unit="money",
                    symbol=match.group(1) or "",
                    match_start=match.start(),
                    match_end=match.end(),
                    ref=index,
                    queried_entities=queried_entities,
                    scale=scale,
                )
            )
        for match in _FIGURE_PERCENT_RE.finditer(text):
            figures.append(
                _bind_figure(
                    text=text,
                    match_text=match.group(0).strip(),
                    value=float(match.group(1).replace(",", "")),
                    unit="percent",
                    symbol="",
                    match_start=match.start(),
                    match_end=match.end(),
                    ref=index,
                    queried_entities=queried_entities,
                    scale=1.0,
                )
            )
    ordered: list = []
    for figure in figures:
        if figure["text"] not in seen:
            seen.add(figure["text"])
            ordered.append(figure)
    return ordered[:_SYNTH_FIGURES_MAX]


# Semantic-binding patterns for snippet figures (all generic, topic-free).
_FIGURE_CONTEXT_CHARS = 200
_FIGURE_FREQUENCY_RE = re.compile(
    r"\b(annual\w*|yearly|quarterly|monthly|weekly|daily|"
    r"per\s+(year|quarter|month|week|day))\b",
    re.IGNORECASE,
)
_FIGURE_YEAR_RE = re.compile(
    r"\b((?:F\.?\s*Y\.?\s*)?20\d{2}|fiscal\s+(?:year\s+)?20\d{2})\b",
    re.IGNORECASE,
)
_FIGURE_CURRENCY_WORD_RE = re.compile(
    r"\b(USD|US\s*dollars?|CNY|RMB|yuan|JPY|yen|EUR|euros?|INR|rupees?|"
    r"GBP|pounds?|dollars?)\b",
    re.IGNORECASE,
)
_FIGURE_SUBJECT_RE = re.compile(r"^\s*([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,2})")


def _fallback_subject(window: str) -> Optional[str]:
    """Leading capitalized subject of a snippet window ("Acme raised ..." ->
    "Acme"). Used ONLY when the query names no known entities; otherwise an
    unattributed figure stays unattributed (strict path)."""
    match = _FIGURE_SUBJECT_RE.search(window or "")
    if not match:
        return None
    candidate = " ".join(match.group(1).split())
    if len(candidate) < 2:
        return None
    return candidate


def _bind_figure(
    *,
    text: str,
    match_text: str,
    value: float,
    unit: str,
    symbol: str,
    match_start: int,
    match_end: int,
    ref: int,
    queried_entities: list,
    scale: Optional[float] = None,
) -> dict:
    """Attach semantic binding to one raw figure (Phase 4/5, hardened H2/H3).

    Contract:
    - Display `context` stays a short 200-char window around the match.
    - Semantic binding (WHO/WHAT/WHEN/UNIT/CURRENCY/DEFINITION) NEVER uses
      that truncated window: entity/metric/currency/period/frequency are
      resolved against the FULL snippet text (plus a wide ±1000-char
      fallback), so a metric cue 300 chars away still binds correctly.
    - Every figure records WHO/WHAT/WHEN/UNIT/CURRENCY/DEFINITION with
      explicit fields; comparison_eligible follows the strict H2 contract
      (entity + specific metric + numeric value + unit + explicit ISO
      currency for money + definition + source_type). Raw figures are
      always preserved -- ineligible means prose-only, never dropped.
    """
    # Display window (short, human-readable citation context).
    before = max(0, match_start - 120)
    after = min(len(text), match_end + 80)
    window = " ".join(text[before:after].split())
    if len(window) > _FIGURE_CONTEXT_CHARS:
        window = window[:_FIGURE_CONTEXT_CHARS]
    # Binding scope: the FULL snippet text (never truncated before binding).
    # A wide local window is checked first for precision, then the full
    # text as recall backstop so distant cues still bind.
    wide_before = max(0, match_start - 1000)
    wide_after = min(len(text), match_end + 1000)
    wide_scope = " ".join(text[wide_before:wide_after].split())
    full_scope = " ".join(str(text or "").split())
    entity: Optional[str] = None
    try:
        if figure_entity_label is not None:
            entity = figure_entity_label(wide_scope, queried_entities or [])
            if entity is None:
                entity = figure_entity_label(full_scope, queried_entities or [])
    except Exception:
        entity = None
    if entity is None and not queried_entities:
        # No known entities in play: fall back to the window's own subject
        # so "Acme raised $X" vs "Globex sold for $Y" stay attributable.
        entity = _fallback_subject(window) or _fallback_subject(wide_scope)
    metric: Optional[str] = None
    try:
        if figure_metric_label is not None:
            metric = figure_metric_label(wide_scope) or figure_metric_label(full_scope)
    except Exception:
        metric = None
    currency: Optional[str] = None
    if unit == "money":
        try:
            word_match = _FIGURE_CURRENCY_WORD_RE.search(wide_scope) or _FIGURE_CURRENCY_WORD_RE.search(full_scope)
            if normalize_currency is not None:
                currency = normalize_currency(
                    symbol or None,
                    word_match.group(1) if word_match else None,
                )
        except Exception:
            currency = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    try:
        years = [match.group(1) for match in _FIGURE_YEAR_RE.finditer(wide_scope)]
        if not years:
            years = [match.group(1) for match in _FIGURE_YEAR_RE.finditer(full_scope)]
        if years:
            period_start = years[0]
            period_end = years[-1]
    except Exception:
        pass
    frequency: Optional[str] = None
    try:
        freq_match = _FIGURE_FREQUENCY_RE.search(wide_scope) or _FIGURE_FREQUENCY_RE.search(full_scope)
        if freq_match:
            frequency = freq_match.group(1).lower()
    except Exception:
        pass
    # Definition: the specific metric cue IS the definition when no
    # separate definition phrase exists (funding vs startup_cost vs
    # revenue vs cost are different definitions and must never equate).
    definition: Optional[str] = str(metric) if metric else None
    resolved_scale: Optional[float] = None
    try:
        resolved_scale = float(scale) if scale is not None else 1.0
    except (TypeError, ValueError):
        resolved_scale = 1.0
    candidate = {
        "text": match_text,
        "value": value,
        "unit": unit,
        "context": window,
        "full_context": full_scope[:2000],
        "ref": ref,
        "entity": entity,
        "metric": metric,
        "currency": currency,
        "scale": resolved_scale,
        "period_start": period_start,
        "period_end": period_end,
        "frequency": frequency,
        "definition": definition,
        "source_type": "snippet",
        "comparison_eligible": False,
    }
    # Strict H2 eligibility (never just entity+metric): money needs an
    # explicit ISO currency; generic metric labels fail.
    try:
        if is_figure_comparison_eligible is not None:
            eligible, _ = is_figure_comparison_eligible(candidate)
        else:
            eligible = bool(entity and metric) and not (
                unit == "money" and not currency
            ) and str(metric or "").lower() not in ("money", "currency", "unknown", "")
        candidate["comparison_eligible"] = bool(eligible)
    except Exception:
        candidate["comparison_eligible"] = False
    return candidate


def _figures_table_visual(figures: list) -> Optional[VisualOutput]:
    if not figures:
        return None
    return VisualOutput(
        visual_type="table",
        title="Figures cited",
        props={
            "columns": ["Figure", "Context"],
            "values": [
                [f"{figure['text']} [{figure['ref']}]", figure["context"]]
                for figure in figures
            ],
        },
    )


def _figures_bar_visual(
    figures: list, query: Optional[str] = None
) -> Optional[VisualOutput]:
    """Bar chart over same-class figures (money with money, percent with
    percent) using normalized values; labels carry citation numbers.

    Contract (Phase 6/13, hardened H2/H3): on an explicit comparison query
    ONLY strictly eligible figures (H2 contract) may chart -- untyped
    figures cannot become comparison evidence -- AND figures with
    EXPLICITLY different metric cues (funding vs startup_cost, revenue vs
    cost) never share one bar (the 504999900% root cause). Qualitative
    (non-comparison) queries keep the legacy leniency (same unit, no
    explicit metric conflict), since that bar is a cited-amounts
    illustration, not a like-for-like claim.
    """
    pool = list(figures or [])
    try:
        if query and is_comparison_query is not None and is_comparison_query(query):
            eligible = [fig for fig in pool if fig.get("comparison_eligible")]
            if len(eligible) < 2:
                logger.info(
                    "Figures bar blocked: fewer than 2 semantically bound "
                    "figures for a comparison query."
                )
                return None
            pool = eligible
            # H3: metric agreement is mandatory for comparison bars, not
            # just for comparison cards. funding vs startup_cost must not
            # bar-chart together even when both are eligible on their own.
            if figures_share_metric is not None:
                try:
                    shares, detail = figures_share_metric(pool)
                except Exception:
                    shares, detail = True, ""
                if not shares:
                    logger.info("Figures bar blocked: %s.", detail)
                    return None
            # H5: money bars in KNOWN different currencies never compare.
            try:
                known = {
                    str(fig.get("currency", "") or "").strip().upper()
                    for fig in pool if fig.get("unit") == "money"
                }
                known.discard("")
                if len(known) > 1:
                    logger.info(
                        "Figures bar blocked: mixed currencies %s.", sorted(known)
                    )
                    return None
            except Exception as exc:
                # Fail-closed: currency check unavailable -> block the bar.
                logger.warning("Figure currency check failed, blocking bar: %s", exc)
                return None
    except Exception as exc:
        # Fail-closed: eligibility check unavailable -> no comparison bar.
        logger.warning("Figure eligibility check failed, blocking bar: %s", exc)
        return None
    by_unit: Dict[str, list] = {}
    for figure in pool:
        by_unit.setdefault(figure["unit"], []).append(figure)
    candidates = [group for group in by_unit.values() if len(group) >= 2]
    if not candidates:
        return None
    # Prefer the largest same-unit group whose metrics agree; a group with
    # conflicting cues is skipped rather than charted (H3).
    ordered = sorted(candidates, key=len, reverse=True)
    group: Optional[list] = None
    if figures_share_metric is not None:
        for candidate in ordered:
            try:
                shares, _ = figures_share_metric(candidate)
            except Exception:
                shares = True
            if shares:
                group = candidate
                break
        if group is None:
            logger.info("Figures bar blocked: no metric-agreeing group.")
            return None
    else:
        group = ordered[0]
    assert group is not None
    unit_word = "amount" if group[0]["unit"] == "money" else "percent"
    return VisualOutput(
        visual_type="graph",
        title=f"Cited {unit_word}s compared",
        props={
            "chart_type": "bar",
            "labels": [f"{figure['context'][:36]} [{figure['ref']}]" for figure in group],
            "datasets": [
                {
                    "name": unit_word,
                    "values": [figure["value"] for figure in group],
                }
            ],
        },
    )


def _sources_table_visual(web_sources: list) -> Optional[VisualOutput]:
    """Honest visual for qualitative web answers: the actual cited sources as
    a single-column table of titles, so even a prose answer carries an
    artifact. Titles only — every row resolves through the same provider, so
    a provider column is noise. Provider-only entries (no URL) are included -
    the frontend renders them without a link instead of dropping the
    citation."""
    rows = [
        [str(source.get("title", "Source"))[:80]]
        for source in (web_sources or [])
        if str(source.get("title", "")).strip()
    ][:_SYNTH_SOURCES_MAX_ROWS]
    if not rows:
        return None
    return VisualOutput(
        visual_type="table",
        title="Sources cited",
        props={"columns": ["Source"], "values": rows},
    )


def _timeline_visual(
    news_context: list, web_sources: list
) -> Optional[VisualOutput]:
    """Dated timeline for 'what's going on with X': one row per dated snippet
    (date + trimmed event text), newest first. Needs per-snippet dates (#2);
    returns None when fewer than 2 dated snippets exist."""
    rows: list[list[str]] = []
    for index, snippet in enumerate(news_context or []):
        if index >= len(web_sources or []):
            break
        date = ((web_sources[index] or {}).get("published_date") or "").strip()
        if not date:
            continue
        event = " ".join(str(snippet).split())[:120]
        rows.append([date[:10], f"{event} [{index + 1}]"])
    if len(rows) < 2:
        return None
    rows = sorted(rows, key=lambda row: row[0], reverse=True)[
        :_SYNTH_SOURCES_MAX_ROWS
    ]
    return VisualOutput(
        visual_type="table",
        title="Timeline",
        props={"columns": ["Date", "Event"], "values": rows},
    )


def _comparison_from_figures(
    figures: list, query: str
) -> Optional[VisualOutput]:
    """Comparison card for explicit 'X vs Y' web questions: first two
    same-unit figures become value/baseline, all become labeled groups.
    Only fires on comparative intent with 2+ comparable figures.

    Hardened against the funding-vs-cost false comparison (shared root
    cause of the tech-startup vs robotics-startup and NVIDIA vs AMD
    failures): figures with EXPLICITLY different metric cues (funding vs
    cost, revenue vs cost, ...) never compare, and -- when the query names
    its entities -- each figure must mention one of them so unrelated
    figures cannot enter the comparison.
    """
    if not COMPARISON_INTENT_RE.search(query or ""):
        return None
    # Phase 4/5 contract: raw untyped figures never enter numerical
    # comparison. Only semantically bound figures (entity AND metric known)
    # are eligible; the rest stay citable prose in the figures table.
    pool = [fig for fig in (figures or []) if fig.get("comparison_eligible")]
    if len(pool) < 2:
        if figures:
            logger.info(
                "Comparison blocked: fewer than 2 semantically bound figures "
                "(untyped figures cannot become comparison evidence)."
            )
        return None
    # Phase 11: money figures in KNOWN different currencies never compare.
    try:
        known_currencies = {
            str(fig.get("currency", "") or "").strip().upper() for fig in pool
            if fig.get("unit") == "money"
        }
        known_currencies.discard("")
        if len(known_currencies) > 1:
            logger.info(
                "Comparison blocked: mixed figure currencies %s.",
                sorted(known_currencies),
            )
            return None
    except Exception as exc:
        # Fail-closed: currency check unavailable -> block comparison.
        logger.warning("Figure currency check failed, blocking comparison: %s", exc)
        return None
    by_unit: Dict[str, list] = {}
    for figure in pool:
        by_unit.setdefault(figure.get("unit"), []).append(figure)
    candidates = [group for group in by_unit.values() if len(group) >= 2]
    if not candidates:
        return None
    # Prefer the largest same-unit group, but drop any group whose figures
    # carry conflicting metric cues (total funding vs startup cost, ...).
    ordered = sorted(candidates, key=len, reverse=True)
    group: Optional[list] = None
    if figures_share_metric is not None:
        for candidate in ordered:
            try:
                shares, _ = figures_share_metric(candidate)
            except Exception:
                # Fail-closed: metric-agreement check unavailable -> skip group.
                logger.warning("Metric-agreement check failed, skipping group.")
                continue
            if shares:
                group = candidate[:6]
                break
        if group is None:
            logger.info("Comparison blocked: figures carry mismatched metrics.")
            return None
    else:
        group = ordered[0][:6]
    # Entity attribution: when the query names comparison entities, every
    # figure used must mention one of them. Unrelated money figures (random
    # snippet values that never name NVIDIA/AMD, tech/robotics, ...) cannot
    # form a "comparison".
    try:
        entities: list = []
        if decompose_comparison_query is not None:
            entities = (decompose_comparison_query(query or "") or {}).get("entities", []) or []
        if entities and figure_entity_label is not None:
            attributed = [
                fig for fig in group
                if figure_entity_label(str(fig.get("context", "")), entities)
            ]
            # Need at least two figures covering at least two DISTINCT
            # entities -- otherwise this is one side (or no side) talking.
            covered = {
                str(figure_entity_label(str(fig.get("context", "")), entities) or "").lower()
                for fig in attributed
            }
            covered.discard("")
            if len(attributed) < 2 or len(covered) < 2:
                logger.info(
                    "Comparison blocked: figures not attributable to distinct "
                    f"queried entities {entities}."
                )
                return None
            group = attributed[:6]
    except Exception as exc:
        # Fail-closed: attribution check unavailable -> block comparison.
        logger.warning("Figure-entity attribution check failed, blocking: %s", exc)
        return None
    return VisualOutput(
        visual_type="comparison",
        title="Comparison",
        props={
            "value": group[0]["value"],
            "baseline": group[1]["value"],
            "groups": [
                {
                    "label": f"{figure['context'][:36]} [{figure['ref']}]",
                    "value": figure["value"],
                }
                for figure in group
            ],
        },
    )


def _outlook_status_visual(
    news_context: list, query: str
) -> Optional[VisualOutput]:
    """Citation-backed outlook badge for sentiment/opinion questions with no
    hard numbers: word-count sentiment over the snippets maps to
    on_track/at_risk/off_track. Detail always cites snippet numbers."""
    if not (news_context or []):
        return None
    if not SENTIMENT_INTENT_RE.search(query or ""):
        return None
    blob = " ".join(str(snippet).lower() for snippet in news_context)
    words = re.findall(r"[a-z]+", blob)
    if not words:
        return None
    positive = sum(1 for word in words if word in _POSITIVE_WORDS)
    negative = sum(1 for word in words if word in _NEGATIVE_WORDS)
    if positive > negative:
        state = "on_track"
        summary = "Sources lean positive"
    elif negative > positive:
        state = "off_track"
        summary = "Sources lean negative"
    else:
        state = "at_risk"
        summary = "Sources are mixed"
    top_ref = 1
    return VisualOutput(
        visual_type="status",
        title="Outlook",
        props={
            "state": state,
            "detail": f"{summary} across {len(news_context)} cited snippet(s) [1-{len(news_context)}]; top signal [{top_ref}].",
        },
    )


def _fundamentals_comparison_visual(
    fundamentals: list,
    query: Optional[str] = None,
) -> Optional[VisualOutput]:
    """Structured comparison from Yahoo fundamentals (no regex): market caps
    side by side when 2+ entities resolved.

    A CURRENT snapshot can never satisfy a historical growth/profitability
    request: when the query asks for "last N years", this returns None so a
    snapshot market-cap card cannot masquerade as a 3-year comparison.
    """
    try:
        if query and decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query) or {}
            if decomposed.get("requires_history"):
                logger.info(
                    "Snapshot fundamentals comparison blocked for historical query."
                )
                return None
    except Exception as exc:
        # Fail-closed: historical gate unavailable -> block snapshot comparison.
        logger.warning("Historical gate check failed, blocking comparison: %s", exc)
        return None
    caps = [
        item
        for item in (fundamentals or [])
        if isinstance(item, dict) and isinstance(item.get("market_cap"), (int, float))
    ]
    if len(caps) < 2:
        return None
    # Phase 11: market caps in KNOWN different currencies must not compare
    # as absolutes (CNY 100B next to USD 100B). Unknown currency on any side
    # passes with the code shown in the label when known.
    try:
        known = {
            str(item.get("currency", "") or "").strip().upper()
            for item in caps
        }
        known.discard("")
        if len(known) > 1:
            logger.info(
                "Snapshot fundamentals comparison blocked: mixed currencies %s.",
                sorted(known),
            )
            return None
    except Exception as exc:
        # Fail-closed: currency check unavailable -> block absolutes comparison.
        logger.warning("Currency check failed, blocking comparison: %s", exc)
        return None
    caps = caps[:6]
    return VisualOutput(
        visual_type="comparison",
        title="Market cap comparison",
        props={
            "value": caps[0]["market_cap"],
            "baseline": caps[1]["market_cap"],
            "groups": [
                {
                    "label": str(item.get("entity", item.get("symbol", "entity"))),
                    "value": item["market_cap"],
                }
                for item in caps
            ],
        },
    )


# --- Grounding: the LLM may propose comparison/graph visuals for web-only
# evidence; trust there is checked, not implicit. Every numeric value in the
# visual must appear in the cited snippets (formatting slop allowed) or the
# visual is discarded for the deterministic figures fallback. ---

_VISUAL_NUMBER_RE = re.compile(
    r"([$€₹£])?\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|B|million|billion|thousand|%|percent)?"
)
_VISUAL_SCALE = {
    "k": 1e3, "K": 1e3, "thousand": 1e3,
    "M": 1e6, "million": 1e6,
    "B": 1e9, "billion": 1e9,
}


def _parse_scaled_number(text: str) -> list[float]:
    values: list[float] = []
    for match in _VISUAL_NUMBER_RE.finditer(text or ""):
        if not match.group(2):
            continue
        raw_digits = match.group(2).replace(",", "")
        # Bare 4-digit years (2024) are dates, not plottable data - skip them
        # only when there is no money/percent/scale marker attached.
        if not match.group(1) and not match.group(3):
            if raw_digits in ("2022", "2023", "2024", "2025", "2026", "2027"):
                continue
            if len(raw_digits) == 4 and raw_digits.startswith(("19", "20")):
                continue
        try:
            amount = float(raw_digits)
        except ValueError:
            continue
        scale = _VISUAL_SCALE.get(match.group(3) or "", 1)
        values.append(amount * scale)
    return values


def _iter_visual_numbers(props: Any) -> list[float]:
    """All plottable numbers in a visual's props: numeric leaves verbatim +
    scaled numbers parsed from money/percent-like strings. Label-only date
    strings contribute nothing (no money/percent marker, years skipped)."""
    found: list[float] = []
    stack = [props]
    while stack:
        node = stack.pop()
        if isinstance(node, bool):
            continue
        if isinstance(node, (int, float)):
            found.append(float(node))
        elif isinstance(node, str):
            # Only strings that look like figures ($, %, k/M/B) count;
            # plain category labels ("east", "Jan 01") are ignored.
            if re.search(r"[$€₹£%]", node) or re.search(
                r"\b(k|K|M|B|million|billion|thousand|percent)\b", node
            ):
                found.extend(_parse_scaled_number(node))
            elif re.fullmatch(r"\s*[\d,]+(\.\d+)?\s*", node):
                try:
                    found.append(float(node.replace(",", "").strip()))
                except ValueError:
                    pass
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return found


def _visual_numbers_grounded(visual: VisualOutput, snippets: list) -> bool:
    """True when every number in the visual appears in the snippets.

    Both sides go through the same scaled parser ($300k == 300000), so
    formatting slop is allowed but invented magnitudes are not. Years are
    excluded on both sides (dates, not data). Tolerance is tight (1%) to
    absorb rounding, not to bless nearby-but-different figures.
    """
    numbers = _iter_visual_numbers(visual.props)
    if not numbers:
        return True  # qualitative cards (sources table, outlook) need no grounding
    snippet_values: list[float] = []
    for snippet in snippets or []:
        snippet_values.extend(_parse_scaled_number(str(snippet)))
    if not snippet_values:
        return False
    for value in numbers:
        grounded = any(
            abs(candidate - value)
            <= max(1e-6, abs(value) * 0.01, abs(candidate) * 0.01)
            for candidate in snippet_values
        )
        if not grounded:
            return False
    return True


def _evidence_numbers(
    snippets: Optional[list] = None,
    rows: Optional[Sequence[dict]] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    market_data: Optional[list] = None,
    computed_numbers: Optional[dict] = None,
) -> list[float]:
    """Every validated numeric value across all evidence channels (P0#13).

    Uniform grounding pool: a displayed number is traceable when it matches
    any validated evidence value or deterministic computation output --
    regardless of visual type or channel. Row presence never disables this.
    """
    pool: list[float] = []
    try:
        for snippet in snippets or []:
            pool.extend(_parse_scaled_number(str(snippet)))
    except Exception:
        pass
    try:
        for row in list(rows or [])[:500]:
            if not isinstance(row, dict):
                continue
            for value in row.values():
                if _is_number(value):
                    pool.append(float(value))
                elif isinstance(value, str):
                    pool.extend(_parse_scaled_number(value))
    except Exception:
        pass
    try:
        for lst in (list(price_history or []) + list(market_data or [])):
            if isinstance(lst, dict):
                for value in list(lst.get("values") or [])[:500]:
                    if _is_number(value):
                        pool.append(float(value))
        for item in list(financial_history or []):
            if not isinstance(item, dict):
                continue
            for block_key in ("revenue", "net_income"):
                block = (item.get(block_key, {}) or {})
                if isinstance(block, dict):
                    for value in list(block.get("values") or [])[:500]:
                        if _is_number(value):
                            pool.append(float(value))
    except Exception:
        pass
    try:
        stack = [computed_numbers or {}]
        while stack:
            node = stack.pop()
            if _is_number(node):
                pool.append(float(node))
            elif isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, (list, tuple)):
                stack.extend(node)
    except Exception:
        pass
    return pool


def _visual_numbers_grounded_in_pool(visual: VisualOutput, pool: list[float]) -> bool:
    """True when every number in the visual appears in the evidence pool."""
    numbers = _iter_visual_numbers(visual.props)
    if not numbers:
        return True  # qualitative cards need no grounding
    if not pool:
        return False
    for value in numbers:
        grounded = any(
            abs(candidate - value)
            <= max(1e-6, abs(value) * 0.01, abs(candidate) * 0.01)
            for candidate in pool
        )
        if not grounded:
            return False
    return True


def drop_ungrounded_visuals(
    visuals: list, snippets: list
) -> tuple[list, int]:
    """Split LLM-proposed visuals into (kept, dropped_count).

    Uniform grounding (P0#13): applies to EVERY visual type with numbers --
    no row-based exemption. Callers with structured evidence should prefer
    drop_ungrounded_visuals_evidence (full pool); this snippet variant is
    kept for web-only paths.
    """
    kept: list = []
    dropped = 0
    for visual in visuals or []:
        try:
            if _visual_numbers_grounded(visual, snippets):
                kept.append(visual)
            else:
                dropped += 1
                logger.info(
                    "Dropped ungrounded %s visual '%s'.",
                    getattr(visual, "visual_type", "?"),
                    getattr(visual, "title", "")[:60],
                )
        except Exception as exc:
            # Fail-closed: a grounding check that itself throws must drop
            # the visual, never keep an unverified number.
            logger.warning("Grounding check failed, dropping visual: %s", exc)
            dropped += 1
    return kept, dropped


def drop_ungrounded_visuals_evidence(
    visuals: list,
    *,
    snippets: Optional[list] = None,
    rows: Optional[Sequence[dict]] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    market_data: Optional[list] = None,
    computed_numbers: Optional[dict] = None,
) -> tuple[list, int]:
    """Uniform grounding across all channels and visual types (P0#13/P0#14).

    No exemptions for row-derived, table, or financial-history visuals: any
    displayed numeric value must be traceable to validated evidence or a
    deterministic computation. Fail-closed on checker exceptions.
    """
    pool = _evidence_numbers(
        snippets=snippets, rows=rows, price_history=price_history,
        financial_history=financial_history, market_data=market_data,
        computed_numbers=computed_numbers,
    )
    kept: list = []
    dropped = 0
    for visual in visuals or []:
        try:
            if _visual_numbers_grounded_in_pool(visual, pool):
                kept.append(visual)
            else:
                dropped += 1
                logger.info(
                    "Dropped ungrounded %s visual '%s' (evidence pool).",
                    getattr(visual, "visual_type", "?"),
                    getattr(visual, "title", "")[:60],
                )
        except Exception as exc:
            logger.warning("Grounding check failed, dropping visual: %s", exc)
            dropped += 1
    return kept, dropped


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
        numeric_count = sum(1 for value in present if _is_number(value))
        # A column stays numeric when numbers dominate: isolated
        # missing/non-numeric cells ("n/a") are skipped downstream, never
        # 0-filled, and must not demote the whole column to text.
        if numeric_count >= 2 and numeric_count >= len(present) / 2:
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
        # Missing/non-numeric points are OMITTED (label and value together),
        # never coerced to literal 0: zero and missing are semantically
        # different, and a 0 invents a data point the evidence never had.
        labels: list = []
        values: list = []
        for row in sample:
            cell = row.get(numeric)
            if not _is_number(cell):
                continue
            labels.append(str(row.get(date_col)))
            values.append(cell)
        if len(labels) >= 3:
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
        # Metric-aware aggregation (P0#17): non-additive metrics (price,
        # margin, average, median, rate, percent, ratio) must never be
        # summed. SUM only for additive totals; otherwise AVG (mean of the
        # bucket), recorded explicitly in the title.
        _NON_ADDITIVE_RE = re.compile(
            r"(price|margin|average|avg|median|rate|percent|pct|ratio|"
            r"pe_ratio|p\/e|score|index)",
            re.IGNORECASE,
        )
        _agg = "AVG" if _NON_ADDITIVE_RE.search(str(numeric)) else "SUM"
        _bucket_vals: Dict[str, list] = {}
        for row in sample:
            key = str(row.get(category_col))
            value = row.get(numeric)
            # Non-numeric cells contribute nothing (never +0): a missing
            # value must not fabricate a zero-height bar or inflate totals.
            if not _is_number(value):
                continue
            _bucket_vals.setdefault(key, []).append(float(value))
        buckets: Dict[str, float] = {}
        for key, vals in _bucket_vals.items():
            if not vals:
                continue
            buckets[key] = (
                round(sum(vals) / len(vals), 2) if _agg == "AVG" else vals[0] + sum(vals[1:])
            )
        if 2 <= len(buckets) <= 12:
            _title = f"{numeric} by {category_col}"
            if _agg == "AVG":
                _title += " [avg]"
            parts["graph"] = {
                "visual_type": "graph",
                "title": _title,
                "props": {
                    "chart_type": "bar",
                    "labels": list(buckets.keys()),
                    "datasets": [{"name": f"{numeric} ({_agg})", "values": list(buckets.values())}],
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


def _align_series(
    series: list, max_points: int = _SYNTH_SERIES_MAX_POINTS
) -> tuple[list, list]:
    """Align multi-series timestamps before rendering (Phase 15 contract).

    Never assumes the first series' labels apply to every series: builds the
    sorted union of all timestamps, maps each series onto it (explicit None
    for missing observations -- never a neighbor's value, never 0), then
    downsamples the ALIGNED axis so labels and every dataset stay in lockstep.
    Returns (labels, datasets). Timestamps normalize to ISO day strings when
    parseable so "2024-01-01" and "Jan 01" style mixes still align; otherwise
    raw label text is the key.

    Semantic-channel guard (P0#15/#16): series with different stated
    metric/definition/frequency/unit/channel are NOT aligned together --
    the caller must filter first. As defense in depth, an explicit
    channel marker mismatch (macro vs market) raises instead of unioning.
    """
    from datetime import datetime as _datetime

    def _key(label: Any) -> str:
        text = str(label)
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%b %d", "%b %d, %Y", "%Y/%m/%d"):
            try:
                parsed = _datetime.strptime(
                    text[: len(fmt)] if fmt.startswith("%b") else text[:10], fmt
                )
                if fmt == "%b %d":
                    return f"--{parsed.month:02d}-{parsed.day:02d}"
                return parsed.date().isoformat()
            except (ValueError, OverflowError):
                continue
        try:
            return _datetime.fromisoformat(text[:10]).date().isoformat()
        except (ValueError, TypeError):
            return text

    # Defense in depth: refuse to union series from different semantic
    # channels (e.g. FRED macro series mixed into market_data).
    try:
        channels = {
            str((item or {}).get("channel", "") or "").strip().lower()
            for item in (series or [])
            if isinstance(item, dict) and str((item or {}).get("channel", "") or "").strip()
        }
        if len(channels) > 1:
            raise ValueError(f"refusing to align semantically different channels {sorted(channels)}")
        metrics = {
            str((item or {}).get("metric", "") or "").strip().lower()
            for item in (series or [])
            if isinstance(item, dict) and str((item or {}).get("metric", "") or "").strip()
        }
        # "close" vs "close" aligns; "close" vs "cpi" never does.
        _MACRO_METRICS = {"cpi", "inflation", "unemployment", "gdp", "fed_funds", "treasury"}
        if metrics & _MACRO_METRICS and len(metrics) > 1:
            raise ValueError(f"refusing to align macro metric with market metric {sorted(metrics)}")
    except ValueError:
        raise
    except Exception:
        pass
    union: list[str] = []
    seen: set[str] = set()
    display: dict[str, str] = {}
    per_series: list[dict] = []
    for item in series:
        labels_in = list(item.get("labels") or [])
        values = list(item.get("values") or [])
        mapping = {_key(label): value for label, value in zip(labels_in, values)}
        per_series.append(mapping)
        for label in labels_in:
            key = _key(label)
            if key not in seen:
                seen.add(key)
                union.append(key)
                # First-seen original label wins for display, so ISO series
                # keep ISO labels and month-day series keep month-day labels.
                display[key] = str(label)
    union.sort()
    stride = max(1, len(union) // max_points)
    keys = union[::stride]
    labels = [display[key] for key in keys]
    datasets = []
    for item, mapping in zip(series, per_series):
        datasets.append(
            {
                "name": str(item.get("entity", item.get("symbol", "series"))),
                # Explicit None where this series has no observation: the
                # frontend renders a gap, never a fabricated 0.
                "values": [mapping.get(key) for key in keys],
            }
        )
    return labels, datasets


def _market_graph_visual(market_data: list, query: str) -> Optional[VisualOutput]:
    """Generalized market-series graph (replaces the old one-off hardcoded
    stock chart in the route): any entities, downsampled, neutral title.

    Gated: a one-month snapshot series NEVER charts for a multi-year
    request, and a single-entity series NEVER charts for a named
    two-company comparison. Both gates return None so the caller falls
    back to honest sources (never a mismatched chart).
    """
    entities = [item for item in market_data if item.get("values")]
    if not entities:
        return None
    try:
        if decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query or "") or {}
            required = decomposed.get("entities", []) or []
            years = decomposed.get("period_years")
            is_comp = decomposed.get("is_comparison", False)
            # Partial-result policy: a validated subset with >=2 entities
            # may chart (excluded entities stated in prose); only an
            # insufficient subset (<2) blocks. Never zero-fill the missing.
            if is_comp and len(required) >= 2:
                have = {
                    str(item.get("entity", "")).strip().lower()
                    for item in entities
                }
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Market graph partial: missing %s for %s; "
                            "charting validated subset.",
                            missing, required,
                        )
                        # Filter to validated subset only (no placeholders).
                        _have = have
                        entities = [
                            item for item in entities
                            if str(item.get("entity", "")).strip().lower() in _have
                        ]
                    else:
                        logger.info(
                            "Market graph blocked: missing %s for comparison %s.",
                            missing, required,
                        )
                        return None
            # Short-term data cannot satisfy a multi-year request. EVERY
            # series is checked (H9/H14): the first series' span never
            # stands in for the rest.
            if years and validate_historical_coverage is not None:
                for item in entities:
                    ok, detail = validate_historical_coverage(
                        labels=list(item.get("labels") or []),
                        period_start=item.get("period_start"),
                        period_end=item.get("period_end"),
                        requested_years=years,
                        values=item.get("values"),
                    )
                    if not ok:
                        logger.info(
                            "Market graph blocked for historical query: %s: %s",
                            item.get("entity"), detail,
                        )
                        return None
    except Exception as exc:
        # Fail-closed: gate unavailable -> block the graph.
        logger.warning("Market graph gate check failed, blocking graph: %s", exc)
        return None
    entities = entities[:4]
    names = [str(item.get("entity", "series")) for item in entities]
    # Aligned timestamps: every dataset shares the union axis (Phase 15).
    labels, datasets = _align_series(entities)
    return VisualOutput(
        visual_type="graph",
        title=f"{', '.join(names)} performance",
        props={"chart_type": "line", "labels": labels, "datasets": datasets},
    )


def _price_history_graph_visual(
    price_history: list, query: str, requested_years: Optional[int] = None
) -> Optional[VisualOutput]:
    """Validated multi-year price chart over the validated subset.

    Partial-result policy: charts the validated subset (>=2 entities with
    dated multi-year coverage) and never zero-fills the missing entity
    (exclusion stated in prose). Only an insufficient subset (<2) blocks.
    This is the ONLY chart allowed for "last N years" stock performance.
    Never invents.
    """
    series = [item for item in (price_history or []) if item.get("values") and item.get("labels")]
    if len(series) < 2:
        return None
    years = requested_years
    required: list = []
    try:
        if decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query or "") or {}
            if years is None:
                years = decomposed.get("period_years")
            required = list(decomposed.get("entities", []) or [])
            # Partial: filter to validated-coverage subset; block only when
            # fewer than 2 survive. Missing entities are excluded, not filled.
            if decomposed.get("is_comparison") and len(required) >= 2:
                have = {
                    str(item.get("entity", "")).strip().lower() for item in series
                }
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Price-history graph partial: missing %s for %s; "
                            "charting validated subset.",
                            missing, required,
                        )
                    else:
                        logger.info(
                            "Price-history graph blocked: missing %s for %s.",
                            missing, required,
                        )
                        return None
    except Exception as exc:
        # Fail-closed: entity check unavailable -> block the chart.
        logger.warning("Price-history entity check failed, blocking: %s", exc)
        return None
    if years and validate_historical_coverage is not None:
        for item in series:
            ok, _ = validate_historical_coverage(
                labels=list(item.get("labels") or []),
                period_start=item.get("period_start"),
                period_end=item.get("period_end"),
                requested_years=years,
                values=item.get("values"),
            )
            if not ok:
                logger.info(
                    "Price-history graph blocked: %s lacks %sY coverage.",
                    item.get("entity"), years,
                )
                return None
    # Like-for-like: same frequency when stated.
    freqs = {str(item.get("frequency", "") or "").lower() for item in series}
    freqs.discard("")
    if len(freqs) > 1:
        logger.info("Price-history graph blocked: mixed frequencies %s.", freqs)
        return None
    series = series[:4]
    names = [str(item.get("entity", item.get("symbol", "series"))) for item in series]
    # Aligned timestamps: no series inherits the first series' labels.
    # Missing observations render as gaps (None), never as a neighbor's
    # value or 0 (Phase 15).
    labels, datasets = _align_series(series)
    unit = str(series[0].get("currency", "") or "").strip()
    title = f"{', '.join(names)} stock performance"
    if years:
        title += f" ({years}Y)"
    try:
        series_currencies = {
            str(item.get("currency", "") or "").strip().upper() for item in series
        }
        series_currencies.discard("")
        if len(series_currencies) > 1:
            title += f" [mixed currencies {sorted(series_currencies)}: compare trends, not levels]"
        elif unit:
            title += f" [{unit}]"
    except Exception:
        if unit:
            title += f" [{unit}]"
    return VisualOutput(
        visual_type="graph",
        title=title,
        props={"chart_type": "line", "labels": labels, "datasets": datasets},
    )


def _financial_history_table_visual(
    financial_history: list, metric: str = "revenue", query: Optional[str] = None,
) -> Optional[VisualOutput]:
    """Annual revenue / net-income table over the validated subset.

    Partial-result policy: tables the validated subset (>=2 companies with
    start+end rows); missing entities are excluded with prose notice, never
    zero-filled. Values are raw retrieved figures; growth/margin math lives
    in comparison_stats. Currency shown per row (absolutes in different
    currencies never like-for-like -- compare deterministic growth %).
    """
    rows: list[list[str]] = []
    by_entity: Dict[str, int] = {}
    currencies: set[str] = set()
    for item in financial_history or []:
        block = (item or {}).get(metric, {}) if isinstance(item, dict) else None
        if not isinstance(block, dict) or not block.get("values"):
            continue
        entity = str(item.get("entity", item.get("symbol", "entity")))
        currency = str(
            block.get("currency", "") or (item or {}).get("currency", "") or ""
        ).strip().upper()
        labels = list(block.get("labels") or [])
        values = list(block.get("values") or [])
        count = 0
        for label, value in zip(labels, values):
            try:
                rows.append(
                    [entity, str(label)[:10], f"{float(value):,.0f}", currency or "?"]
                )
                count += 1
            except (TypeError, ValueError):
                continue
        if currency:
            currencies.add(currency)
        by_entity[entity] = by_entity.get(entity, 0) + count
    # Every represented company needs start AND end; at least two overall.
    # Partial: drop single-point entities, keep the validated subset.
    by_entity = {k: v for k, v in by_entity.items() if v >= 2}
    rows = [r for r in rows if r[0] in by_entity]
    currencies = {r[3] for r in rows if r[3] and r[3] != "?"}
    if len(by_entity) < 2:
        return None
    # Named-comparison partial: validated subset charts; missing named
    # entities are excluded (prose notice), never zero-filled.
    if query and decompose_comparison_query is not None:
        try:
            required = (decompose_comparison_query(query) or {}).get("entities", []) or []
            if len(required) >= 2:
                have = {str(k).strip().lower() for k in by_entity}
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Financial table partial: missing %s for %s; "
                            "tabling validated subset.",
                            missing, required,
                        )
                    else:
                        logger.info(
                            "Financial table blocked: missing %s for %s.",
                            missing, required,
                        )
                        return None
        except Exception as exc:
            # Fail-closed: entity check unavailable -> block the table.
            logger.warning("Financial table entity check failed, blocking: %s", exc)
            return None
    # Interleaved (never sorted-then-cut): a row cap must cost years, never
    # a whole company.
    rows = _interleave_entities(sorted(rows), _SYNTH_FINANCIAL_MAX_ROWS)
    base = "Annual revenue" if metric == "revenue" else "Annual net income"
    if len(currencies) == 1:
        title = f"{base} [{next(iter(currencies))}]"
    elif len(currencies) > 1:
        # Mixed currencies: absolutes are NOT comparable -- growth % is.
        title = (
            f"{base} [mixed currencies {sorted(currencies)}: compare "
            "growth %, not absolutes]"
        )
    else:
        title = f"{base} [currency as reported]"
    return VisualOutput(
        visual_type="table",
        title=title,
        props={"columns": ["Company", "Fiscal year", "Value", "Currency"], "values": rows},
    )


def _margin_table_visual(financial_history: list, query: Optional[str] = None) -> Optional[VisualOutput]:
    """Net-profit-margin table (net income / revenue * 100, computed here).

    One comparable profitability metric for every validated company.
    Partial: tables the validated subset (>=2 with computable margins);
    missing entities excluded with prose notice, never zero-filled.
    """
    rows: list[list[str]] = []
    for item in financial_history or []:
        if not isinstance(item, dict):
            continue
        entity = str(item.get("entity", item.get("symbol", "entity")))
        rev = (item.get("revenue", {}) or {})
        inc = (item.get("net_income", {}) or {})
        rev_labels = list(rev.get("labels", []) or [])
        rev_values = list(rev.get("values", []) or [])
        inc_labels = list(inc.get("labels", []) or [])
        inc_values = list(inc.get("values", []) or [])
        if compute_net_margins is None:
            return None
        try:
            margins = compute_net_margins(
                rev_values, inc_values,
                revenue_labels=rev_labels or None,
                income_labels=inc_labels or None,
            )
        except Exception:
            continue
        for label, margin in zip(rev_labels, margins):
            if isinstance(margin, (int, float)):
                rows.append([entity, str(label)[:10], f"{margin:.2f}%"])
    entities = {row[0] for row in rows}
    if len(entities) < 2:
        return None
    # Named-comparison partial: validated subset tables; missing excluded.
    if query and decompose_comparison_query is not None:
        try:
            required = (decompose_comparison_query(query) or {}).get("entities", []) or []
            if len(required) >= 2:
                have = {str(name).strip().lower() for name in entities}
                want = {str(name).strip().lower() for name in required}
                missing = sorted(want - have)
                if missing:
                    if len(have) >= 2:
                        logger.info(
                            "Margin table partial: missing %s for %s; "
                            "tabling validated subset.",
                            missing, required,
                        )
                    else:
                        logger.info(
                            "Margin table blocked: missing %s for %s.",
                            missing, required,
                        )
                        return None
        except Exception as exc:
            # Fail-closed: entity check unavailable -> block the table.
            logger.warning("Margin table entity check failed, blocking: %s", exc)
            return None
    rows = _interleave_entities(sorted(rows), _SYNTH_FINANCIAL_MAX_ROWS)
    return VisualOutput(
        visual_type="table",
        title="Net profit margin (net income / revenue)",
        props={"columns": ["Company", "Fiscal year", "Net margin"], "values": rows},
    )


def _historical_comparison_gate(
    query: str,
    market_data: Optional[list] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    fundamentals: Optional[list] = None,
) -> Dict[str, Any]:
    """Evaluate whether a historical comparison may be charted.

    Returns a dict with: entities/metrics/years, per-metric evidence
    presence, historical_ok + detail, comparison_ok + detail, blocked
    (bool), blocked_reason, and deterministic comparison_stats when the
    evidence validates. Pure gating -- never fabricates data.
    """
    empty: Dict[str, Any] = {
        "applies": False, "blocked": False, "blocked_reason": "",
        "entities": [], "metrics": [], "years": None,
        "historical_ok": True, "historical_detail": "",
        "comparison_ok": True, "comparison_detail": "",
        "comparison_stats": None,
    }
    try:
        if decompose_comparison_query is None:
            return empty
        decomposed = decompose_comparison_query(query or "") or {}
    except Exception:
        return empty
    entities = list(decomposed.get("entities", []) or [])
    metrics = list(decomposed.get("metrics", []) or [])
    years = decomposed.get("period_years")
    is_comp = bool(decomposed.get("is_comparison"))
    requires_history = bool(decomposed.get("requires_history"))
    # Gate applies to named multi-entity comparisons (historical or not):
    # single-series and mismatched evidence must never chart as "comparison".
    if not (is_comp and len(entities) >= 2):
        return empty
    # Scope early-out: when NO requested entity can enter market adapters
    # (all countries/geographies, all private/unlisted companies, all
    # concepts...), this market-evidence gate is inapplicable -- the
    # question is a qualitative/snippet comparison, not a Yahoo-chartable
    # one. Without this, "Compare America and Britain" burns through
    # stock/revenue validation and fails loudly with exclusion boilerplate
    # for entities that were never market candidates.
    try:
        from app.services.data.comparison import (
            market_candidate_entities as _market_candidates,
        )

        if not _market_candidates(entities):
            return {**empty, "entities": entities, "metrics": metrics, "years": years}
    except Exception:
        pass
    gate: Dict[str, Any] = {
        **empty, "applies": True, "entities": entities,
        "metrics": metrics, "years": years,
    }
    if not requires_history:
        # Non-historical named comparison: only enforce entity coverage
        # (both sides present) -- no period validation needed.
        have = set()
        for lst in (market_data or [], price_history or []):
            for item in lst:
                if isinstance(item, dict) and item.get("values"):
                    have.add(str(item.get("entity", "")).strip().lower())
        want = {str(e).strip().lower() for e in entities}
        if not want.issubset(have):
            # Fall back to snippet-figure attribution downstream; do not
            # hard-block non-historical comparisons here (figures may still
            # carry both sides). Mark not-blocked so old paths run.
            pass
        return gate

    # Historical path: need structured multi-year evidence for EVERY
    # requested metric. Snippets alone never suffice.
    price_history = list(price_history or [])
    financial_history = list(financial_history or [])
    details: list[str] = []
    comp_details: list[str] = []
    historical_ok = True
    comparison_ok = True

    def _entities_with_price() -> set[str]:
        return {
            str(item.get("entity", "")).strip().lower()
            for item in price_history
            if isinstance(item, dict) and item.get("values") and item.get("labels")
        }

    def _entities_with_fin(block: str) -> set[str]:
        out: set[str] = set()
        for item in financial_history:
            if not isinstance(item, dict):
                continue
            chunk = item.get(block, {})
            if isinstance(chunk, dict) and chunk.get("values") and chunk.get("labels"):
                out.add(str(item.get("entity", "")).strip().lower())
        return out

    want = {str(e).strip().lower() for e in entities}
    stats_input: Dict[str, Dict[str, Tuple[Any, Any]]] = {}
    scope_word = f"all {len(entities)}" if len(entities) > 2 else "both"
    # Partial tracking: per-metric validated subsets (never zero-filled).
    validated_by_metric: Dict[str, List[str]] = {}
    excluded_by_metric: Dict[str, List[str]] = {}

    # -- stock performance: multi-year price history (validated subset) --
    if METRIC_STOCK in metrics:
        have = _entities_with_price()
        missing_stock = sorted(want - have)
        # Coverage-filtered subset: only entities with history + span.
        covered: List[dict] = []
        for item in price_history:
            ok, detail = validate_historical_coverage(
                labels=list(item.get("labels") or []),
                period_start=item.get("period_start"),
                period_end=item.get("period_end"),
                requested_years=years,
                values=item.get("values"),
            )
            if not ok:
                details.append(f"{item.get('entity')}: {detail}")
            else:
                covered.append(item)
        if missing_stock:
            details.append(
                f"stock price history missing for {missing_stock} "
                f"(have {sorted(have)})."
            )
        if len(covered) >= 2:
            # Like-for-like among the COVERED subset only (not all requested).
            ev = []
            for item in covered:
                vals = list(item.get("values") or [])
                labs = list(item.get("labels") or [])
                if ComparisonEvidence is None:
                    continue
                try:
                    ev.append(ComparisonEvidence(
                        entity=str(item.get("entity", "")),
                        metric=METRIC_STOCK,
                        value=float(vals[-1]),
                        unit=str(item.get("currency", "price") or "price"),
                        period_start=str(labs[0]) if labs else None,
                        period_end=str(labs[-1]) if labs else None,
                        frequency=str(item.get("frequency", "weekly") or "weekly"),
                        definition=str(item.get("metric", "close") or "close"),
                        source="Yahoo Finance",
                        source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/history/",
                        is_historical=True,
                    ))
                except (TypeError, ValueError):
                    continue
            # Validate subset like-for-like (no expected_entities=all gate).
            subset_names = [str(item.get("entity", "")) for item in covered]
            ok, detail = validate_comparison(ev, expected_entities=subset_names, expected_metric=METRIC_STOCK,
                                             enforce_currency=False) if len(ev) >= 2 else (False, "fewer than 2 stock series")
            if not ok:
                comp_details.append(f"stock: {detail}")
            else:
                for item in covered:
                    vals = list(item.get("values") or [])
                    stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_STOCK] = (vals[0], vals[-1])
                validated_by_metric[METRIC_STOCK] = [str(i.get("entity", "")) for i in covered]
        else:
            comp_details.append(f"stock comparison lacks {scope_word} companies (only {len(covered)} validated).")
        # Excluded for this metric: requested minus validated.
        _validated_lower = {str(e).strip().lower() for e in validated_by_metric.get(METRIC_STOCK, [])}
        excluded_by_metric[METRIC_STOCK] = [e for e in entities if str(e).strip().lower() not in _validated_lower]

    # -- revenue growth: annual revenue history (validated subset) --
    if METRIC_REVENUE in metrics:
        have = _entities_with_fin("revenue")
        missing_rev = sorted(want - have)
        if missing_rev:
            details.append(f"annual revenue history missing for {missing_rev}.")
        # Subset with 2+ annual points.
        rev_covered = []
        for item in financial_history:
            chunk = item.get("revenue", {})
            vals = list(chunk.get("values") or [])
            if len(vals) >= 2:
                rev_covered.append(item)
            else:
                details.append(f"{item.get('entity')}: need 2+ annual revenue points.")
        if len(rev_covered) >= 2:
            ev = []
            for item in rev_covered:
                chunk = item.get("revenue", {})
                labs = list(chunk.get("labels") or [])
                vals = list(chunk.get("values") or [])
                if ComparisonEvidence is not None:
                    _ccy = str(
                            chunk.get("currency", "") or item.get("currency", "") or ""
                        ).strip().upper() or None
                    try:
                        ev.append(ComparisonEvidence(
                            entity=str(item.get("entity", "")),
                            metric=METRIC_REVENUE,
                            value=float(vals[-1]),
                            unit=_ccy or "currency",
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency="annual",
                            definition=str(chunk.get("metric", "annualTotalRevenue")),
                            source="Yahoo Finance",
                            source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/financials/",
                            is_historical=True,
                            currency=_ccy,
                            source_type="yahoo" if item.get("provenance") != "web_snippets" else "web_snippets",
                            provenance=str(item.get("provenance", "") or "") or None,
                        ))
                    except (TypeError, ValueError):
                        continue
            subset_names = [str(item.get("entity", "")) for item in rev_covered]
            ok, detail = validate_comparison(ev, expected_entities=subset_names, expected_metric=METRIC_REVENUE,
                                       enforce_currency=False) if len(ev) >= 2 else (False, "fewer than 2 revenue series")
            if not ok:
                comp_details.append(f"revenue: {detail}")
            else:
                for item in rev_covered:
                    vals = list((item.get("revenue", {}) or {}).get("values") or [])
                    if len(vals) >= 2:
                        stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_REVENUE] = (vals[0], vals[-1])
                validated_by_metric[METRIC_REVENUE] = [str(i.get("entity", "")) for i in rev_covered]
        else:
            comp_details.append(f"revenue comparison lacks {scope_word} companies (only {len(rev_covered)} validated).")
        _validated_lower = {str(e).strip().lower() for e in validated_by_metric.get(METRIC_REVENUE, [])}
        excluded_by_metric[METRIC_REVENUE] = [e for e in entities if str(e).strip().lower() not in _validated_lower]

    # -- profitability: ONE shared annual metric (validated subset) --
    # (net profit margin = net income / revenue * 100, computed in code).
    if METRIC_PROFIT in metrics:
        have = _entities_with_fin("net_income")
        missing_prof = sorted(want - have)
        if missing_prof:
            details.append(f"annual profitability history missing for {missing_prof}.")
        prof_covered = []
        for item in financial_history:
            chunk = item.get("net_income", {})
            vals = list(chunk.get("values") or [])
            if len(vals) >= 2:
                prof_covered.append(item)
            else:
                details.append(f"{item.get('entity')}: need 2+ annual profit points.")
        if len(prof_covered) >= 2:
            ev = []
            for item in prof_covered:
                chunk = item.get("net_income", {})
                labs = list(chunk.get("labels") or [])
                vals = list(chunk.get("values") or [])
                if ComparisonEvidence is not None:
                    _ccy2 = str(
                            chunk.get("currency", "") or item.get("currency", "") or ""
                        ).strip().upper() or None
                    try:
                        ev.append(ComparisonEvidence(
                            entity=str(item.get("entity", "")),
                            metric=METRIC_PROFIT,
                            value=float(vals[-1]),
                            unit=_ccy2 or "currency",
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency="annual",
                            definition=str(chunk.get("metric", "annualNetIncome")),
                            source="Yahoo Finance",
                            source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/financials/",
                            is_historical=True,
                            currency=_ccy2,
                            source_type="yahoo" if item.get("provenance") != "web_snippets" else "web_snippets",
                            provenance=str(item.get("provenance", "") or "") or None,
                        ))
                    except (TypeError, ValueError):
                        continue
            subset_names = [str(item.get("entity", "")) for item in prof_covered]
            ok, detail = validate_comparison(ev, expected_entities=subset_names, expected_metric=METRIC_PROFIT,
                                       enforce_currency=False) if len(ev) >= 2 else (False, "fewer than 2 profit series")
            if not ok:
                comp_details.append(f"profitability: {detail}")
            else:
                for item in prof_covered:
                    vals = list((item.get("net_income", {}) or {}).get("values") or [])
                    if len(vals) >= 2:
                        stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_PROFIT] = (vals[0], vals[-1])
                validated_by_metric[METRIC_PROFIT] = [str(i.get("entity", "")) for i in prof_covered]
        else:
            comp_details.append(f"profitability comparison lacks {scope_word} companies (only {len(prof_covered)} validated).")
        _validated_lower = {str(e).strip().lower() for e in validated_by_metric.get(METRIC_PROFIT, [])}
        excluded_by_metric[METRIC_PROFIT] = [e for e in entities if str(e).strip().lower() not in _validated_lower]

    # A 1-month market_data series present WITHOUT price history is the
    # exact live failure: flag it explicitly, never let it satisfy history.
    # It invalidates the STOCK metric subset (other metrics may still be
    # sufficient for a partial answer).
    if not price_history and (market_data or []) and METRIC_STOCK in metrics:
        details.append(
            "only a short-term (one-month) price series is available; "
            "it cannot satisfy a multi-year request."
        )
        comp_details.append("stock: short-term series cannot satisfy history.")
        validated_by_metric.pop(METRIC_STOCK, None)
        excluded_by_metric[METRIC_STOCK] = list(entities)

    # Partial sufficiency: at least one requested metric with >=2 validated
    # entities whose subset passed like-for-like. Missing entities/metrics
    # are excluded with reasons, never zero-filled.
    validated_metrics = sorted(validated_by_metric.keys())
    validated_entities_union = sorted({
        e for ents in validated_by_metric.values() for e in ents
    })
    sufficient = bool(validated_metrics and len(validated_entities_union) >= 2)
    # Historical/comparison verdicts describe the VALIDATED SUBSET, not the
    # full request: True when sufficient, False only when insufficient.
    historical_ok = bool(sufficient)
    comparison_ok = bool(sufficient)
    # Preserve explicit subset failures (e.g. mixed frequencies) as not-ok.
    # If every validated subset failed validation, validated_by_metric would
    # be empty and sufficient False -- already covered.
    gate["historical_ok"] = historical_ok
    gate["historical_detail"] = " ".join(details)
    gate["comparison_ok"] = comparison_ok
    gate["comparison_detail"] = " ".join(comp_details)
    gate["validated_entities"] = validated_entities_union
    gate["validated_metrics"] = validated_metrics
    gate["validated_by_metric"] = {k: list(v) for k, v in validated_by_metric.items()}
    gate["excluded_by_metric"] = {k: list(v) for k, v in excluded_by_metric.items()}
    # Union excluded entities: requested minus validated union.
    _val_lower = {str(e).strip().lower() for e in validated_entities_union}
    gate["excluded_entities"] = [
        e for e in entities if str(e).strip().lower() not in _val_lower
    ]
    _any_metric_excluded = any(
        bool(v) for v in (excluded_by_metric or {}).values()
    )
    gate["partial"] = bool(sufficient and (
        len(gate["excluded_entities"]) > 0
        or _any_metric_excluded
        or len(validated_metrics) < len([m for m in metrics if m in (METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT)])
    ))
    # Currency transparency (Phase 11): growth % and net margins are
    # currency-invariant (per-entity math), but absolute values in KNOWN
    # different currencies must never be read as like-for-like. Record the
    # mix for assumptions/table titles; absolute-value outputs enforce it.
    currency_sets: Dict[str, set] = {}
    try:
        for item in price_history:
            code = str(item.get("currency", "") or "").strip().upper()
            if code:
                currency_sets.setdefault("stock", set()).add(code)
        for item in financial_history:
            for block_key in ("revenue", "net_income"):
                block = (item or {}).get(block_key, {}) or {}
                code = str(
                    block.get("currency", "") or item.get("currency", "") or ""
                ).strip().upper()
                if code:
                    currency_sets.setdefault(block_key, set()).add(code)
    except Exception:
        pass
    mixed = {key: sorted(codes) for key, codes in currency_sets.items() if len(codes) > 1}
    gate["currency_mixed"] = mixed
    gate["currency_detail"] = (
        "Reported currencies differ across companies "
        f"{mixed}: growth % and net margins compare currency-invariant; "
        "absolute values do not compare without explicit FX conversion."
        if mixed else ""
    )
    gate["blocked"] = not (historical_ok and comparison_ok)
    # Exclusion transparency for partial answers (never silent, never raw
    # repr): structured entity/metric lists rendered through the single
    # canonical renderer (exclusion_note_for) -- no f"{list}" interpolation
    # anywhere near user-facing text.
    try:
        from app.services.data.canonical import exclusion_note_for as _excl_for

        _validated_by_metric = validated_by_metric or {}
        _fully_excluded = list(gate.get("excluded_entities", []) or [])
        _fully_lower = {str(e).strip().lower() for e in _fully_excluded}
        # A metric with zero validated entities is excluded as a whole;
        # a metric validated for the subset names who lacks it per entity
        # ("Umbrella (revenue_growth, profitability)"), so partial gaps
        # are never silent and never raw repr.
        _metric_bits: list = []
        _partial_map: dict = {}
        for metric, excluded in (excluded_by_metric or {}).items():
            if not excluded:
                continue
            if _validated_by_metric.get(metric):
                for entity in excluded:
                    if str(entity).strip().lower() not in _fully_lower:
                        _partial_map.setdefault(str(entity), []).append(str(metric))
            else:
                _metric_bits.append({"metric": str(metric)})
        _entity_bits = [{"entity": entity} for entity in _fully_excluded]
        for entity, metrics in _partial_map.items():
            _entity_bits.append({"entity": f"{entity} ({', '.join(metrics)})"})
        gate["excluded_metrics"] = [
            bit["metric"] for bit in _metric_bits
        ]
        gate["exclusion_note"] = (
            _excl_for(
                {
                    "excluded_entities": _entity_bits,
                    "excluded_metrics": _metric_bits,
                }
            )
            if gate.get("partial")
            else ""
        )
    except Exception:
        gate["exclusion_note"] = ""
    if gate["blocked"] and insufficient_reason is not None:
        try:
            gate["blocked_reason"] = insufficient_reason(
                query=query, entities=entities, metrics=metrics,
                requested_years=years, historical_ok=historical_ok,
                historical_detail=gate["historical_detail"],
                comparison_ok=comparison_ok,
                comparison_detail=gate["comparison_detail"],
            )
        except Exception:
            gate["blocked_reason"] = "insufficient validated evidence."
    elif not gate["blocked"] and compute_comparison_stats is not None and stats_input:
        try:
            yearly: Dict[str, Any] = {}
            latest_margins: Dict[str, float] = {}
            period_spans: List[str] = []
            for item in financial_history:
                if not isinstance(item, dict):
                    continue
                entity = str(item.get("entity", ""))
                rev = (item.get("revenue", {}) or {})
                inc = (item.get("net_income", {}) or {})
                rev_labels = list(rev.get("labels", []) or [])
                rev_values = list(rev.get("values", []) or [])
                inc_labels = list(inc.get("labels", []) or [])
                inc_values = list(inc.get("values", []) or [])
                entry_yearly: Dict[str, Any] = {}
                if rev_values and compute_yearly_stats is not None:
                    entry_yearly["revenue"] = compute_yearly_stats(
                        rev_labels, rev_values
                    )
                if inc_values and compute_yearly_stats is not None:
                    entry_yearly["net_income"] = compute_yearly_stats(
                        inc_labels, inc_values
                    )
                if (
                    rev_values and inc_values
                    and compute_net_margins is not None
                ):
                    margins = compute_net_margins(rev_values, inc_values)
                    entry_yearly["net_margin"] = [
                        {"label": label, "value": margin}
                        for label, margin in zip(rev_labels, margins)
                    ]
                    for margin in reversed(margins):
                        if isinstance(margin, (int, float)):
                            latest_margins[entity] = margin
                            break
                if entry_yearly:
                    yearly[entity] = entry_yearly
                if rev_labels:
                    period_spans.append(
                        f"{entity}: {rev_labels[0]} -> {rev_labels[-1]}"
                    )
            period_basis = (
                "Fiscal-year labels as reported ("
                + ("; ".join(period_spans) if period_spans else "no annual labels")
                + "); growth compares each company's latest vs earliest "
                "completed annual period in-window."
            )
            if gate.get("currency_detail"):
                period_basis += " " + str(gate["currency_detail"])
            gate["comparison_stats"] = compute_comparison_stats(
                stats_input,
                latest_margins or None,
                yearly or None,
                period_basis,
            )
        except Exception as exc:
            logger.warning("Comparison stats failed: %s", exc)
            gate["comparison_stats"] = None
    return gate


def _fail_closed_gate(query: str, reason: str) -> Dict[str, Any]:
    """Blocked gate for when correctness validation itself throws.

    Fail-closed: a validator exception must BLOCK comparison/chart, never
    continue with existing visuals. `applies` is derived from a guarded
    decomposition so non-comparison queries (own-data rows, sentiment
    prose) keep their normal path; comparison queries get applies=True,
    blocked=True with the failure recorded as the blocked reason.
    """
    entities: list = []
    metrics: list = []
    years: Optional[int] = None
    applies = False
    try:
        if decompose_comparison_query is not None:
            decomposed = decompose_comparison_query(query or "") or {}
            entities = list(decomposed.get("entities", []) or [])
            metrics = list(decomposed.get("metrics", []) or [])
            years = decomposed.get("period_years")
            applies = bool(decomposed.get("is_comparison")) and len(entities) >= 2
    except Exception:
        applies = False
    return {
        "applies": applies,
        "blocked": True if applies else False,
        "blocked_reason": str(reason or "")[:500],
        "entities": entities,
        "metrics": metrics,
        "years": years,
        "historical_ok": False,
        "historical_detail": str(reason or "")[:300],
        "comparison_ok": False,
        "comparison_detail": str(reason or "")[:300],
        "comparison_stats": None,
        "validation_failed": True,
    }


def log_runtime_trace(trace: Dict[str, Any]) -> None:
    """Emit one safe structured runtime-trace line (H15)."

    Never logs payloads, keys, tokens, or rows -- only the trace contract
    (counts, names, gate verdict, confidence). Safe to leave on in prod.
    """
    try:
        if format_runtime_trace is not None:
            logger.info("RUNTIME TRACE %s", format_runtime_trace(trace))
        else:
            logger.info(
                "RUNTIME TRACE query=%r gate=%s visuals=%s confidence=%s",
                str((trace or {}).get("query", ""))[:120],
                (trace or {}).get("comparison_gate"),
                (trace or {}).get("visual_decision"),
                (trace or {}).get("final_confidence"),
            )
    except Exception as exc:
        logger.warning("Runtime trace log failed: %s", exc)


# ---------------------------------------------------------------------------
# Visual provenance contract + single validated-evidence state + staleness.
# Every visual carries requested intent, entities, metric, units, timeframe,
# frequency, evidence/source IDs, data points, computation IDs. Before
# rendering, the visual's data must match the answer's validated evidence;
# entity/metric/timeframe/units/source/value mismatches or a different
# semantic intent reject the visual (fail closed). What-if queries never
# reuse market-history charts; follow-ups regenerate when entity/metric/
# period/intent changes.
# ---------------------------------------------------------------------------
_WHAT_IF_RE = re.compile(
    r"\bwhat\s+(?:\w+\s+){0,4}if\b|\bscenario\b|\bassume\b.*\b(grow|drop|rise|fall|increase|decrease)",
    re.IGNORECASE,
)


def is_what_if_query(query: str) -> bool:
    """True for what-if / scenario intent (generic, not price-only)."""
    try:
        from app.services.data.stats import parse_what_if as _parse_wif

        if _parse_wif(query or "") is not None:
            return True
    except Exception:
        pass
    if _WHAT_IF_RE.search(query or ""):
        return True
    # Conditional multi-lever scenarios ("If price +25%, lose 18% of
    # customers, ..., calculate the new revenue") don't say "what if" but
    # are the same intent -- the query states its own baseline numbers
    # with no uploaded data needed. Two detection paths: (a) the extractor
    # itself confidently parses a baseline + lever, or (b) a lighter regex
    # backstop for cases the extractor can't cleanly parse a baseline for
    # (still worth routing away from market-history/historical-comparison
    # visuals even when no number can be computed).
    try:
        from app.services.data.stats import (
            compute_freeform_scenario as _compute_freeform,
        )

        if _compute_freeform(query or "") is not None:
            return True
    except Exception:
        pass
    try:
        from app.services.data.stats import CONDITIONAL_SCENARIO_RE as _cond_re

        if _cond_re.search(query or ""):
            return True
    except Exception:
        pass
    return False


def _visual_intent(query: str) -> str:
    """Requested semantic intent: what_if | historical_comparison | comparison | rows | qualitative."""
    q = query or ""
    if is_what_if_query(q):
        return "what_if"
    try:
        if decompose_comparison_query is not None:
            d = decompose_comparison_query(q) or {}
            if d.get("is_comparison") and d.get("requires_history"):
                return "historical_comparison"
            if d.get("is_comparison"):
                return "comparison"
    except Exception:
        pass
    if re.search(CHART_INTENT_RE, q, re.IGNORECASE):
        return "chart"
    return "qualitative"


def build_visual_provenance(
    *,
    query: str,
    entities: Sequence[str],
    metric: Optional[str] = None,
    units: Optional[str] = None,
    timeframe: Optional[str] = None,
    frequency: Optional[str] = None,
    source_ids: Optional[Sequence[str]] = None,
    computation_ids: Optional[Sequence[str]] = None,
    data_points: Optional[Any] = None,
) -> Dict[str, Any]:
    """Provenance payload attached to every synthesized visual."""
    try:
        time_label = timeframe
        if time_label is None and decompose_comparison_query is not None:
            d = decompose_comparison_query(query or "") or {}
            time_label = d.get("period_label")
    except Exception:
        time_label = timeframe
    return {
        "intent": _visual_intent(query),
        "entities": list(entities or []),
        "metric": metric,
        "units": units,
        "timeframe": time_label,
        "frequency": frequency,
        "source_ids": list(source_ids or []),
        "computation_ids": list(computation_ids or []),
        "data_points": data_points,
    }


def attach_provenance(visual: VisualOutput, provenance: Dict[str, Any]) -> VisualOutput:
    """Attach provenance to a visual (mutates and returns it)."""
    try:
        visual.provenance = dict(provenance or {})
    except Exception:
        pass
    return visual


def compute_structured_what_if(
    financial_history: Optional[list], query: str
) -> Optional[Dict[str, Any]]:
    """Deterministic what-if scenario from validated financial history.

    Generic across arbitrary entities: extracts a signed percent from the
    query ("grow 10%", "drop 5%", "increase by 12%") and scales each
    entity's latest annual revenue by that factor (quantity-unaffected
    assumption, same as the row-level what-if). Returns a single aggregate
    scenario (baseline_total / scenario_total / delta + per-entity table)
    for narration to quote verbatim -- never LLM arithmetic. None when no
    percent or no revenue history. Pure.
    """
    try:
        text = query or ""
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s?%", text)
        if not pct_match:
            return None
        try:
            pct = float(pct_match.group(1))
        except (TypeError, ValueError):
            return None
        # Direction from surrounding words; bare "change 10%" defaults up.
        span = text[max(0, pct_match.start() - 60):pct_match.end() + 20]
        down = bool(re.search(
            r"\b(lower|drop\w*|decreas\w*|down|cut\w*|reduc\w*|less|fewer|fall\w*|declin\w*)\b",
            span, re.IGNORECASE,
        ))
        up = bool(re.search(
            r"\b(rais\w*|ris\w*|rose|increas\w*|up|higher|hik\w*|more|grow\w*|gain\w*)\b",
            span, re.IGNORECASE,
        ))
        if down and not up:
            pct = -pct
        if pct == 0:
            return None
        factor = 1.0 + pct / 100.0
        if factor <= 0:
            return None
        per_entity: Dict[str, Dict[str, float]] = {}
        baseline_total = 0.0
        for item in (financial_history or []):
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity", item.get("symbol", "")) or "").strip()
            if not entity:
                continue
            chunk = (item.get("revenue", {}) or {})
            vals = list(chunk.get("values") or [])
            if not vals:
                continue
            try:
                latest = float(vals[-1])
            except (TypeError, ValueError):
                continue
            scenario = latest * factor
            per_entity[entity] = {
                "baseline": round(latest, 2),
                "scenario": round(scenario, 2),
                "delta": round(scenario - latest, 2),
            }
            baseline_total += latest
        if not per_entity:
            return None
        scenario_total = round(baseline_total * factor, 2)
        baseline_total = round(baseline_total, 2)
        return {
            "target": "revenue",
            "pct_change": pct,
            "factor": round(factor, 4),
            "baseline_total": baseline_total,
            "scenario_total": scenario_total,
            "delta": round(scenario_total - baseline_total, 2),
            "per_entity": per_entity,
            "basis": "latest annual revenue per entity scaled by the scenario factor",
            "assumption": (
                "Assumes quantity/mix unaffected by the change "
                "(no elasticity modeled)."
            ),
        }
    except Exception as exc:
        logger.warning("Structured what-if failed: %s", exc)
        return None


def _visual_entities(visual: VisualOutput) -> List[str]:
    """Entities a visual claims (datasets/groups/labels), lowercased."""
    out: List[str] = []
    try:
        props = visual.props or {}
        for ds in (props.get("datasets") or []):
            name = str((ds or {}).get("name", "") or "").strip()
            if name and name.lower() not in ("amount", "percent", "value", "series"):
                out.append(name)
        for grp in (props.get("groups") or []):
            label = str((grp or {}).get("label", "") or "").strip()
            # Group labels carry citations ("Acme ... [1]"); take head token.
            head = re.split(r"[\s\[\(,;]+", label)[0] if label else ""
            if head:
                out.append(label)
        # Table first-column entities: only explicit entity columns
        # ("Company"/"Entity") claim entities. Figure/source listings carry
        # verbatim cited text, not entity claims (their entities live in
        # provenance, not in the display column).
        if visual.visual_type == "table":
            cols = list(props.get("columns") or [])
            vals = list(props.get("values") or [])
            if cols and vals and cols[0].lower() in ("company", "entity"):
                for row in vals[:8]:
                    if row:
                        out.append(str(row[0])[:60])
    except Exception:
        pass
    return out


def validate_visual_provenance(
    visual: VisualOutput,
    *,
    query: str,
    validated_entities: Sequence[str],
    validated_metrics: Sequence[str],
    expected_timeframe: Optional[str] = None,
    allowed_intents: Optional[Sequence[str]] = None,
) -> Tuple[bool, str]:
    """Validate one visual against the answer's validated evidence.

    Rejects when entity/metric/timeframe/units/source untraceable, values
    cannot be traced, or semantic intent differs. Fail-closed: any check
    exception rejects. Pure.
    """
    try:
        prov = getattr(visual, "provenance", None) or {}
        intent = str(prov.get("intent", "") or _visual_intent(query))
        expected_intent = _visual_intent(query)
        # What-if never reuses market-history intent and vice versa.
        if expected_intent == "what_if" and intent == "historical_comparison":
            return False, "stale intent: market-history chart for what-if query"
        if expected_intent == "historical_comparison" and intent == "what_if":
            return False, "stale intent: what-if visual for historical query"
        if allowed_intents and intent not in list(allowed_intents):
            return False, f"intent mismatch: {intent} not in {list(allowed_intents)}"
        # Entity must be within validated set (subset allowed for partial).
        try:
            want = {str(e).strip().lower() for e in (validated_entities or []) if str(e).strip()}
            if want:
                claimed = _visual_entities(visual)
                # Only enforce when the visual names concrete entities.
                named = [c for c in claimed if c and len(c) >= 2]
                if named:
                    for claim in named:
                        cl = claim.lower()
                        # A claim matches when it mentions a validated entity
                        # (labels carry citations/context, not bare names).
                        if not any(v in cl or cl in v for v in want):
                            # Explicit sources-only policy (P0#14): ONLY the
                            # sources/timeline/outlook qualitative tables are
                            # provenance-exempt (they list citations, not
                            # comparison entities). Data tables (figures,
                            # financial, results) obey the same entity
                            # contract as charts.
                            title = str(getattr(visual, "title", "") or "").lower()
                            if visual.visual_type == "table" and any(
                                k in title for k in ("source", "timeline", "outlook")
                            ):
                                continue
                            return False, f"entity mismatch: {claim!r} not in validated {sorted(want)}"
        except Exception as exc:
            return False, f"entity check unavailable ({exc})"
        # Metric must be within validated set when both state one.
        try:
            prov_metric = str(prov.get("metric", "") or "").strip().lower()
            want_m = {str(m).strip().lower() for m in (validated_metrics or []) if str(m).strip()}
            if prov_metric and want_m and prov_metric not in want_m:
                return False, f"metric mismatch: {prov_metric} not in {sorted(want_m)}"
        except Exception as exc:
            return False, f"metric check unavailable ({exc})"
        # Timeframe must match when both state one.
        try:
            prov_time = str(prov.get("timeframe", "") or "").strip().lower()
            exp_time = str(expected_timeframe or "").strip().lower()
            if prov_time and exp_time and prov_time != exp_time:
                return False, f"timeframe mismatch: {prov_time} vs {exp_time}"
        except Exception as exc:
            return False, f"timeframe check unavailable ({exc})"
        # Provenance must name a traceable source/computation. Tables obey
        # the same contract as charts (P0#14); only the explicit
        # qualitative allowlist (sources/timeline/outlook) is exempt.
        try:
            has_source = bool((prov.get("source_ids") or []))
            has_comp = bool((prov.get("computation_ids") or []))
            has_data = prov.get("data_points") is not None
            _title = str(getattr(visual, "title", "") or "").lower()
            _qualitative = visual.visual_type == "table" and any(
                k in _title for k in ("source", "timeline", "outlook")
            )
            if visual.visual_type in ("graph", "comparison", "table") and not _qualitative:
                if not (has_source or has_comp or has_data):
                    return False, "source untraceable: no source/computation IDs"
        except Exception as exc:
            return False, f"source check unavailable ({exc})"
        return True, "visual provenance valid"
    except Exception as exc:
        return False, f"provenance validation failed ({exc})"


def is_visual_stale_for_query(
    visual: VisualOutput,
    current_query: str,
    prior_query: Optional[str] = None,
) -> Tuple[bool, str]:
    """True when a visual from a prior query must not be reused.

    Stale when entity, metric, or period changed, or when what-if intent
    changed (different computation). A pure presentation change to a chart
    follow-up ("chart that") is NOT stale: visuals regenerate from prior
    rows. Generic across arbitrary entities/metrics/periods. Pure.
    """
    try:
        if not prior_query or not current_query:
            return False, ""
        if decompose_comparison_query is None:
            return False, ""
        cur = decompose_comparison_query(current_query or "") or {}
        prev = decompose_comparison_query(prior_query or "") or {}
        cur_e = {str(e).strip().lower() for e in (cur.get("entities", []) or [])}
        prev_e = {str(e).strip().lower() for e in (prev.get("entities", []) or [])}
        if cur_e and prev_e and cur_e != prev_e:
            return True, f"entity changed {sorted(prev_e)} -> {sorted(cur_e)}"
        cur_m = {str(m).strip().lower() for m in (cur.get("metrics", []) or [])}
        prev_m = {str(m).strip().lower() for m in (prev.get("metrics", []) or [])}
        if cur_m and prev_m and cur_m != prev_m:
            return True, f"metric changed {sorted(prev_m)} -> {sorted(cur_m)}"
        if (cur.get("period_label") or "") != (prev.get("period_label") or ""):
            if cur.get("period_label") or prev.get("period_label"):
                return True, f"period changed {prev.get('period_label')} -> {cur.get('period_label')}"
        cur_intent = _visual_intent(current_query)
        prev_intent = _visual_intent(prior_query)
        if (cur_intent == "what_if") != (prev_intent == "what_if"):
            return True, "what-if intent changed"
        # Chart presentation follow-ups regenerate from prior rows (not stale).
        if cur_intent != prev_intent:
            if re.search(CHART_INTENT_RE, current_query or "", re.IGNORECASE):
                return False, ""
            return True, "intent changed"
        return False, ""
    except Exception as exc:
        # Fail-closed: staleness check unavailable -> treat as stale.
        return True, f"staleness check unavailable ({exc})"


def _attach_history_provenance(
    visual: VisualOutput, query: str, evidence: list, years: Optional[int], metric: Optional[str]
) -> VisualOutput:
    """Attach validated-history provenance to a synthesized visual."""
    try:
        entities = [str(item.get("entity", item.get("symbol", ""))) for item in (evidence or []) if isinstance(item, dict)]
        entities = [e for e in entities if e][:4]
        units: Optional[str] = None
        freq: Optional[str] = None
        try:
            first = next((i for i in (evidence or []) if isinstance(i, dict)), {})
            units = str(first.get("currency", "") or "").strip() or None
            freq = str(first.get("frequency", "") or "").strip() or None
        except Exception:
            pass
        source_ids = [f"yahoo:{e}" for e in entities]
        attach_provenance(visual, build_visual_provenance(
            query=query, entities=entities, metric=metric, units=units,
            timeframe=f"{years}Y" if years else None, frequency=freq,
            source_ids=source_ids, computation_ids=["comparison_stats"] if metric else [],
            data_points={"entities": entities, "metric": metric},
        ))
    except Exception as exc:
        logger.warning("Provenance attach failed: %s", exc)
    return visual


def _provenance_filter_final(
    visuals: list, query: str, gate: Dict[str, Any]
) -> list:
    """Final provenance gate for synthesized visuals (fail closed)."""
    out: list = []
    try:
        val_ents = list(gate.get("validated_entities", []) or gate.get("entities", []) or [])
        val_mets = list(gate.get("validated_metrics", []) or gate.get("metrics", []) or [])
        years = gate.get("years")
        time_label = f"{years}Y" if years else None
        # When the gate did not apply, fall back to plan entities ONLY for
        # comparison queries. Own-data row visuals (metric-based, no entities)
        # must not be entity-gated, or every row chart would be rejected.
        if not val_ents and not gate.get("applies"):
            try:
                if build_research_plan is not None:
                    pd = dict(build_research_plan(query) or {})
                    if pd.get("is_comparison") and len(pd.get("entities", []) or []) >= 2:
                        val_ents = list(pd.get("entities", []) or [])
                        val_mets = list(pd.get("metrics", []) or [])
                        time_label = pd.get("period_label") or time_label
            except Exception:
                pass
        for visual in visuals or []:
            vtype = getattr(visual, "visual_type", "")
            _vtitle = str(getattr(visual, "title", "") or "").lower()
            _qualitative_table = vtype == "table" and any(
                k in _vtitle for k in ("source", "timeline", "outlook")
            )
            if vtype not in ("graph", "comparison", "table") or _qualitative_table:
                out.append(visual)
                continue
            try:
                ok, why = validate_visual_provenance(
                    visual, query=query,
                    validated_entities=val_ents,
                    validated_metrics=val_mets,
                    expected_timeframe=time_label,
                )
            except Exception as exc:
                ok, why = False, f"provenance check failed ({exc})"
            if ok:
                out.append(visual)
            else:
                logger.info("Rejected synthesized visual: %s.", why)
    except Exception as exc:
        logger.warning("Final provenance filter failed, stripping charts: %s", exc)
        out = [v for v in (visuals or []) if getattr(v, "visual_type", "") not in ("graph", "comparison")]
    return out


def build_validated_evidence_state(
    *,
    query: str,
    plan: Optional[Dict[str, Any]] = None,
    gate: Optional[Dict[str, Any]] = None,
    completeness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Single canonical validated-evidence state for answer scope, stats,
    confidence, visual planner, visual validator, and narration.

    Merges plan (requested), gate (validated subset + stats), and
    completeness (per-entity coverage) into one dict. Components must read
    this instead of independently deciding whether evidence exists.
    """
    plan = dict(plan or {})
    gate = dict(gate or {})
    completeness = dict(completeness or {})
    entities = list(plan.get("entities", []) or gate.get("entities", []) or [])
    metrics = list(plan.get("metrics", []) or gate.get("metrics", []) or [])
    validated_entities = list(
        gate.get("validated_entities", []) or completeness.get("validated_entities", []) or []
    )
    validated_metrics = list(
        gate.get("validated_metrics", []) or completeness.get("validated_metrics", []) or []
    )
    # Fallback: completeness per_entity matrix.
    if not validated_entities and isinstance(completeness.get("per_entity"), dict):
        for entity, row in (completeness.get("per_entity") or {}).items():
            if isinstance(row, dict) and any(row.values()):
                validated_entities.append(entity)
    if not validated_metrics and isinstance(completeness.get("per_entity"), dict):
        seen: set[str] = set()
        for row in (completeness.get("per_entity") or {}).values():
            if isinstance(row, dict):
                for metric, ok in row.items():
                    if ok and metric not in seen:
                        seen.add(metric)
                        validated_metrics.append(metric)
    excluded_entities = list(
        gate.get("excluded_entities", []) or completeness.get("excluded_entities", []) or []
    )
    if not excluded_entities and entities:
        _val = {str(e).strip().lower() for e in validated_entities}
        excluded_entities = [e for e in entities if str(e).strip().lower() not in _val]
    sufficient = bool(gate.get("comparison_stats")) or bool(
        len(validated_entities) >= 2 and len(validated_metrics) >= 1
    )
    # Gate blocked overrides sufficiency (insufficient subset).
    if gate.get("applies") and gate.get("blocked"):
        sufficient = False
    complete = bool(
        entities and metrics
        and not excluded_entities
        and not (completeness.get("missing") or [])
        and not gate.get("blocked")
    )
    return {
        "query": query,
        "entities_requested": entities,
        "metrics_requested": metrics,
        # Canonical aliases (single state, two key styles during migration;
        # both always agree -- never two interpretations).
        "requested_entities": entities,
        "requested_metrics": metrics,
        "validated_entities": validated_entities,
        "validated_metrics": validated_metrics,
        "excluded_entities": excluded_entities,
        "excluded_metrics": list(completeness.get("excluded_metrics", []) or []),
        "sufficient": sufficient,
        "partial": bool(sufficient and (excluded_entities or gate.get("partial"))),
        "complete": complete,
        "blocked": bool(gate.get("blocked")),
        "comparison_stats": gate.get("comparison_stats"),
        "exclusion_note": str(
            gate.get("exclusion_note", "")
            or completeness.get("exclusion_note", "")
            or ""
        ),
    }


def plan_visuals_from_evidence(
    *,
    query: str,
    validated_state: Optional[Dict[str, Any]] = None,
    has_rows: bool = False,
    has_history: bool = False,
    has_market: bool = False,
    has_snippets: bool = False,
) -> List[str]:
    """Deterministic visual planner (P0#18): the ONE authoritative plan.

    Consumes user intent + canonical validated evidence + visual
    eligibility -- never arbitrary LLM suggestions. The judge's visual_plan
    stays advisory (reconciled + logged by the caller); this wins.
    """
    try:
        intent = _visual_intent(query or "")
        state = validated_state or {}
        blocked = bool(state.get("blocked"))
        sufficient = bool(state.get("sufficient"))
        if intent == "what_if":
            return ["comparison"] if not blocked else []
        if blocked:
            return []
        if state.get("comparison_stats") or (sufficient and has_history):
            # Metric-aware (P0#33): a price graph needs validated stock
            # evidence, tables need validated financial evidence -- never a
            # visual for an unvalidated metric.
            val_mets = {str(m).strip().lower() for m in (state.get("validated_metrics", []) or [])}
            kinds: List[str] = []
            if not val_mets or "stock_performance" in val_mets:
                kinds.append("graph")
            if not val_mets or val_mets & {"revenue_growth", "profitability"}:
                kinds.append("table")
            if state.get("comparison_stats"):
                kinds.append("comparison")
            return kinds
        if has_rows:
            return ["graph", "table", "metric"]
        if has_market:
            return ["graph"]
        if has_snippets:
            if intent == "comparison":
                return ["comparison", "table"]
            return ["table", "insight"]
        return []
    except Exception:
        return []


def apply_narration_contract(
    output: PipelineOutput,
    *,
    validated_state: Optional[Dict[str, Any]] = None,
    computed_numbers: Optional[dict] = None,
    gate: Optional[Dict[str, Any]] = None,
    thinking: Optional[list] = None,
) -> PipelineOutput:
    """Code-enforced narration + final validation + followup grounding.

    - Collects every validated number (evidence + deterministic computation)
      as the grounding pool; flags ungrounded numeric claims.
    - Winners must come from validated comparison stats; otherwise winner
      language is hedged (not silently replaced with a guess).
    - Excluded entities/metrics flagged when presented as included.
    - What-if assumption string must appear verbatim in the answer.
    - Follow-ups dropped when they resurrect excluded entities/metrics.
    - Final consistency actions applied (confidence caps, visual drops).
    Never raises; violations are logged and surfaced via thinking.
    """
    try:
        from app.services.data.canonical import (
            final_response_validation as _final,
            ground_followups as _ground_f,
            validate_narration as _validate,
        )
    except Exception:
        return output
    state = validated_state or {}
    computed_numbers = computed_numbers or {}
    gate = gate or {}
    try:
        pool = _evidence_numbers(
            snippets=None, rows=None,
            price_history=None, financial_history=None,
            market_data=None, computed_numbers=computed_numbers,
        )
        # Winners from the single validated source.
        stats = (computed_numbers.get("comparison_stats", {}) or {})
        if not stats:
            stats = (state.get("deterministic_statistics", {}) or {})
        _gate_stats = gate.get("comparison_stats") or {}
        winners = dict(
            stats.get("winners", {}) or _gate_stats.get("winners", {}) or {}
        )
        verdict = _validate(
            output.answer or "", validated_state=state,
            known_numbers=pool, winners=winners,
        )
        for violation in verdict.get("violations", []):
            logger.info("Narration contract violation: %s", violation[:200])
            if thinking is not None and "ungrounded number" in violation:
                thinking.append("Narration used a number outside validated evidence.")
            if thinking is not None and "winner" in violation:
                thinking.append("Winner claim lacks validated comparison support.")
        # What-if assumption verbatim check (P0#10): the code validates the
        # narrated assumption matches computation state (not just prompting).
        try:
            what_if = computed_numbers.get("what_if") or {}
            assumption = str(what_if.get("assumption", "") or "").strip()
            if assumption and output.clarification is None:
                norm_answer = " ".join((output.answer or "").lower().split())
                norm_assump = " ".join(assumption.lower().split())
                # Key content words of the assumption must appear verbatim
                # (elasticity disclaimer); otherwise append it structurally.
                if "elasticity" in norm_assump and "elasticity" not in norm_answer:
                    output.answer = str(output.answer or "").rstrip() + "\n\n" + assumption
                    if thinking is not None:
                        thinking.append("What-if assumption appended verbatim (was missing).")
        except Exception:
            pass
        # Final consistency validation (P0#23): apply safe actions only
        # (drop visuals, cap confidence) -- never guess replacements.
        try:
            final = _final(
                answer=output.answer or "",
                visuals=list(output.visuals or []),
                confidence=output.confidence,
                validated_state=state,
                known_numbers=pool,
                winners=winners,
            )
            for action in final.get("actions", []):
                if action == "set_confidence_zero":
                    output.confidence = 0.0
                    output.visuals = [
                        v for v in (output.visuals or [])
                        if getattr(v, "visual_type", "") not in ("graph", "comparison")
                    ]
                elif action == "cap_confidence_065":
                    output.confidence = min(float(output.confidence or 0.0), 0.65)
                elif action in ("drop_visual_with_excluded_entity", "drop_unvalidated_visual"):
                    # Drop only the offending visuals is ideal; conservatively
                    # provenance-filter all charts (fail closed, no guessing).
                    output.visuals = _provenance_filter_final(
                        list(output.visuals or []), state.get("query", ""), gate,
                    )
            for violation in final.get("violations", []):
                if thinking is not None:
                    thinking.append(f"Final validation: {violation[:140]}")
        except Exception:
            pass
        # Grounded follow-ups (P1#30).
        try:
            if output.followups:
                output.followups = _ground_f(list(output.followups or []), state)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Narration contract application failed: %s", exc)
    return output


def ensure_visuals(
    output: PipelineOutput,
    *,
    rows: Sequence[dict],
    computed_numbers: Optional[dict],
    market_data: Optional[list],
    web_sources: Optional[list],
    news_context: Optional[list] = None,
    preferred_visual: Optional[str],
    query: str,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
) -> PipelineOutput:
    """Visual guarantee: a validated normal answer must never go out naked
    when plottable tool outputs exist. Clarifications and already-visual
    answers pass through untouched; synthesis only uses real values. Web-only
    answers get cited-figures visuals (comparison for X-vs-Y, bar when
    comparable, timeline when dated, outlook status for sentiment) plus the
    sources table, so even qualitative answers carry multiple cards.

    Final invariant (H10, enforced in CODE): a comparison visualization
    requires comparison_complete == True AND comparison_gate == PASSED AND
    confidence > 0 AND every number originating from validated evidence.
    Historical gating: when the query is a multi-entity historical
    comparison, NO graph/comparison visual is synthesized unless the
    historical gate validates (ALL companies, same metric/unit/period).
    A 0-confidence answer never gains a chart here -- insufficient
    evidence blocks visualization by design.
    Fail-closed: if gate/completeness validation itself throws, comparison
    visuals are BLOCKED (existing graph/comparison cards are stripped and
    only honest sources remain) instead of shipping unverified charts.
    """
    rows = list(rows or [])
    market_data = list(market_data or [])
    web_sources = list(web_sources or [])
    news_context = list(news_context or [])
    fundamentals = list(fundamentals or [])
    macro_data = list(macro_data or [])
    price_history = list(price_history or [])
    financial_history = list(financial_history or [])
    # P0#15: channels stay separate. combined_series is NOT market+macro;
    # the market path charts market_data only, macro never enters it.
    combined_series = list(market_data)
    synthesized: list = []

    def _sources_only() -> list:
        table = _sources_table_visual(web_sources)
        return [table] if table is not None else []

    def _strip_comparison_visuals(visuals: list) -> tuple[list, int]:
        kept = [
            visual for visual in (visuals or [])
            if getattr(visual, "visual_type", "") not in ("graph", "comparison")
        ]
        return kept, len(list(visuals or [])) - len(kept)

    # Historical comparison gate runs BEFORE any synthesis -- and before
    # honoring pre-existing visuals, so a validator exception can never
    # ship an unverified chart.
    gate: Dict[str, Any] = {}
    try:
        gate = _historical_comparison_gate(
            query,
            market_data=market_data,
            price_history=price_history,
            financial_history=financial_history,
            fundamentals=fundamentals,
        )
    except Exception as exc:
        logger.warning("Historical gate failed: blocking comparison visuals: %s", exc)
        gate = _fail_closed_gate(query, f"historical validation unavailable ({exc})")
    # Completeness is authoritative alongside the gate, with partial-result
    # policy: a validated subset (>=2 entities, >=1 common metric) is
    # sufficient for partial visuals with exclusions; only an insufficient
    # subset blocks. Context gaps (fundamentals/snippets) never block alone.
    if gate.get("applies") and check_research_completeness is not None:
        try:
            _plan_for_completeness: Dict[str, Any] = {}
            if build_research_plan is not None:
                try:
                    _plan_for_completeness = dict(build_research_plan(query) or {})
                except Exception:
                    _plan_for_completeness = {
                        "entities": list(gate.get("entities", []) or []),
                        "metrics": list(gate.get("metrics", []) or []),
                        "period_years": gate.get("years"),
                        "requires_history": True,
                        "required_entities": list(gate.get("entities", []) or []),
                        "required_metrics": list(gate.get("metrics", []) or []),
                        "required_tools": [],
                    }
            _completeness = check_research_completeness(
                _plan_for_completeness,
                {
                    "price_history": price_history,
                    "financial_history": financial_history,
                    "market_data": market_data,
                    "fundamentals": fundamentals,
                    "snippets": news_context,
                },
            )
            # Non-blocking context gaps (fundamentals/snippets) are visible
            # in the trace but must not flip a validated gate to BLOCKED:
            # only core history/entity/metric/period failures block here.
            _core_missing = [
                m for m in (_completeness.get("missing") or [])
                if not str(m).startswith("context tool")
            ]
            # Partial: sufficient subset passes even when strict completeness
            # is False; insufficient subset blocks.
            _sufficient = bool(_completeness.get("sufficient_for_partial")) or bool(
                gate.get("validated_entities") and len(gate.get("validated_entities", [])) >= 2
            )
            if not _completeness.get("comparison_complete") and _core_missing and not _sufficient:
                logger.info(
                    "Completeness blocked visualization: missing=%s",
                    (_completeness.get("missing") or [])[:4],
                )
                gate = {
                    **gate,
                    "blocked": True,
                    "blocked_reason": (
                        str(gate.get("blocked_reason", "") or "")
                        + " Completeness: "
                        + "; ".join((_completeness.get("missing") or [])[:4])
                    ).strip(),
                }
            elif _sufficient and gate.get("blocked"):
                # Gate blocked but completeness finds a sufficient validated
                # subset (e.g. gate predates partial): unblock as partial.
                # The gate's own partial path already handles this; this is
                # defense in depth for code paths that skipped it.
                pass
        except Exception as exc:
            # Fail-closed: an unavailable completeness verdict must BLOCK,
            # never let a possibly-partial comparison through.
            logger.warning("Completeness check failed: blocking visuals: %s", exc)
            if gate.get("applies"):
                gate = {
                    **gate,
                    "blocked": True,
                    "blocked_reason": (
                        str(gate.get("blocked_reason", "") or "")
                        + f" Completeness unavailable ({exc}); comparison blocked."
                    ).strip(),
                    "comparison_ok": False,
                }
    if output.clarification is not None:
        return output
    # What-if intent never reuses market-history visuals (no inheritance).
    _is_what_if = is_what_if_query(query)
    if output.visuals:
        # Pre-existing (usually LLM-proposed) visuals: fail-closed filter.
        # BLOCKED gate, zero confidence, stale intent, what-if reuse, or
        # provenance mismatch strips graph/comparison cards; tables/sources
        # prose visuals survive. Every surviving chart is provenance-checked.
        try:
            needs_strip = bool(gate.get("applies") and gate.get("blocked"))
        except Exception:
            needs_strip = True
        try:
            zero_conf = float(output.confidence or 0.0) <= 0.0
        except (TypeError, ValueError):
            zero_conf = True
        if needs_strip or zero_conf:
            output.visuals, stripped = _strip_comparison_visuals(output.visuals)
            if stripped:
                logger.info(
                    "Stripped %d comparison visual(s) from existing visuals "
                    "(blocked=%s, zero_conf=%s).",
                    stripped, needs_strip, zero_conf,
                )
        # What-if: strip any market-history-flavoured chart (stale intent).
        if _is_what_if and output.visuals:
            kept: list = []
            dropped = 0
            for visual in output.visuals:
                try:
                    prov = getattr(visual, "provenance", None) or {}
                    intent = str(prov.get("intent", "") or "").lower()
                    title = str(getattr(visual, "title", "") or "").lower()
                    if getattr(visual, "visual_type", "") in ("graph", "comparison") and (
                        intent == "historical_comparison"
                        or "stock performance" in title
                        or "market" in title
                    ):
                        dropped += 1
                        continue
                except Exception:
                    dropped += 1
                    continue
                kept.append(visual)
            if dropped:
                logger.info("Stripped %d market-history visual(s) for what-if query.", dropped)
            output.visuals = kept
        # Provenance validation for surviving charts (fail closed).
        # Own-data row visuals skip entity gating (metric-based, no entities).
        if output.visuals:
            try:
                _val_ents = list(gate.get("validated_entities", []) or [])
                _val_mets = list(gate.get("validated_metrics", []) or [])
                _time = gate.get("years")
                _time_label = f"{_time}Y" if _time else None
                # Fall back to plan entities ONLY for comparisons; row visuals
                # must not be entity-gated.
                if not _val_ents and not gate.get("applies") and not rows:
                    try:
                        if build_research_plan is not None:
                            _pd = dict(build_research_plan(query) or {})
                            if _pd.get("is_comparison") and len(_pd.get("entities", []) or []) >= 2:
                                _val_ents = list(_pd.get("entities", []) or [])
                                _val_mets = list(_pd.get("metrics", []) or [])
                                _time_label = (_pd.get("period_label") or _time_label)
                    except Exception:
                        pass
                filtered: list = []
                for visual in output.visuals:
                    _vt = getattr(visual, "visual_type", "")
                    _vt_title = str(getattr(visual, "title", "") or "").lower()
                    _qual = _vt == "table" and any(
                        k in _vt_title for k in ("source", "timeline", "outlook")
                    )
                    if _vt not in ("graph", "comparison", "table") or _qual:
                        filtered.append(visual)
                        continue
                    try:
                        ok, why = validate_visual_provenance(
                            visual, query=query,
                            validated_entities=_val_ents,
                            validated_metrics=_val_mets,
                            expected_timeframe=_time_label,
                        )
                    except Exception as exc:
                        ok, why = False, f"provenance check failed ({exc})"
                    # Uniform grounding (P0#13): EVERY visual with numbers
                    # must trace to validated evidence/computation -- no
                    # row-presence exemption.
                    try:
                        if ok:
                            kept_grounded, _ = drop_ungrounded_visuals_evidence(
                                [visual], snippets=news_context, rows=rows,
                                price_history=price_history,
                                financial_history=financial_history,
                                market_data=market_data,
                                computed_numbers=computed_numbers,
                            )
                            if not kept_grounded:
                                ok, why = False, "values untraceable to evidence"
                    except Exception as exc:
                        ok, why = False, f"grounding check failed ({exc})"
                    if ok:
                        filtered.append(visual)
                    else:
                        logger.info("Rejected pre-existing visual: %s.", why)
                output.visuals = filtered
            except Exception as exc:
                # Fail-closed: validation unavailable -> strip charts.
                logger.warning("Provenance validation failed, stripping charts: %s", exc)
                output.visuals, _ = _strip_comparison_visuals(output.visuals)
        return output
    if gate.get("applies") and gate.get("blocked"):
        logger.info("Historical comparison blocked: %s", gate.get("blocked_reason", "")[:160])
        # Blocked comparisons get sources only (honest, not a chart).
        # Fail-closed: any pre-existing graph/comparison is stripped first.
        output.visuals, _ = _strip_comparison_visuals(output.visuals)
        synthesized = _sources_only()
        if synthesized:
            output.visuals = list(output.visuals) + synthesized[:3]
        return output
    # What-if queries never synthesize market-history charts (no inheritance
    # across incompatible intents). They get deterministic scenario visuals
    # from computed what-if numbers plus honest sources only.
    if _is_what_if:
        what_if = (computed_numbers or {}).get("what_if")
        if isinstance(what_if, dict) and what_if:
            try:
                baseline = float(what_if.get("baseline_total", 0) or 0)
                scenario = float(what_if.get("scenario_total", 0) or 0)
                what_visual = VisualOutput(
                    visual_type="comparison",
                    title="What-if scenario",
                    props={
                        "value": scenario, "baseline": baseline,
                        "groups": [
                            {"label": "Baseline", "value": baseline},
                            {"label": "Scenario", "value": scenario},
                        ],
                    },
                )
                attach_provenance(what_visual, build_visual_provenance(
                    query=query, entities=[],
                    metric=str(what_if.get("target", "price")),
                    units=None, timeframe=None, frequency=None,
                    source_ids=["computed:what_if"],
                    computation_ids=["what_if"],
                    data_points={"baseline_total": baseline, "scenario_total": scenario},
                ))
                try:
                    if float(output.confidence or 0.0) > 0.0:
                        synthesized.append(what_visual)
                except (TypeError, ValueError):
                    synthesized.append(what_visual)
            except Exception as exc:
                logger.warning("What-if visual failed: %s", exc)
        sources = _sources_table_visual(web_sources)
        if sources is not None:
            synthesized.append(sources)
        # Tables of raw rows may still help; never market-history graphs.
        if rows:
            try:
                row_visuals = _visuals_from_rows(rows, computed_numbers, preferred_visual)
                for visual in row_visuals:
                    if getattr(visual, "visual_type", "") == "graph":
                        continue
                    try:
                        if getattr(visual, "provenance", None) is None:
                            attach_provenance(visual, build_visual_provenance(
                                query=query, entities=[],
                                metric=None, units=None, timeframe=None, frequency=None,
                                source_ids=["rows"],
                                computation_ids=sorted((computed_numbers or {}).keys()),
                                data_points={"row_count": len(rows)},
                            ))
                    except Exception:
                        pass
                    synthesized.append(visual)
            except Exception as exc:
                logger.warning("What-if row visuals failed: %s", exc)
        if synthesized:
            output.visuals = list(output.visuals) + synthesized[:3]
        # Provenance-validate before returning (fail closed).
        output.visuals = _provenance_filter_final(output.visuals, query, gate)
        return output
    if gate.get("applies") and not gate.get("blocked"):
        # Validated history: chart the validated subset (partial allowed),
        # plus annual tables. Every visual carries provenance.
        years = gate.get("years")
        graph = _price_history_graph_visual(price_history, query, years)
        if graph is not None:
            # At 0 confidence even a validated series stays uncharted.
            try:
                if float(output.confidence or 0.0) > 0.0:
                    _attach_history_provenance(graph, query, price_history, years, METRIC_STOCK)
                    synthesized.append(graph)
            except (TypeError, ValueError):
                _attach_history_provenance(graph, query, price_history, years, METRIC_STOCK)
                synthesized.append(graph)
        for metric_key in ("revenue", "net_income"):
            table = _financial_history_table_visual(financial_history, metric_key, query)
            if table is not None:
                _attach_history_provenance(
                    table, query, financial_history, years,
                    METRIC_REVENUE if metric_key == "revenue" else METRIC_PROFIT,
                )
                synthesized.append(table)
        margin_table = _margin_table_visual(financial_history, query)
        if margin_table is not None:
            _attach_history_provenance(margin_table, query, financial_history, years, METRIC_PROFIT)
            synthesized.append(margin_table)
        sources = _sources_table_visual(web_sources)
        if sources is not None:
            synthesized.append(sources)
        if synthesized:
            logger.info(f"Visual guarantee synthesized {len(synthesized)} validated visual(s).")
            # Validated multi-year evidence earns the full set (graph +
            # annual tables + margin table + sources): capping at 3 here
            # silently dropped the single comparable profitability view.
            output.visuals = list(output.visuals) + synthesized[:5]
        output.visuals = _provenance_filter_final(output.visuals, query, gate)
        return output

    if rows:
        chart_requested = bool(re.search(CHART_INTENT_RE, query, re.IGNORECASE))
        synthesized = _visuals_from_rows(rows, computed_numbers, preferred_visual)
        # Provenance for row visuals (own-data evidence, first-class stage).
        for visual in synthesized:
            try:
                if getattr(visual, "provenance", None) is None:
                    attach_provenance(visual, build_visual_provenance(
                        query=query, entities=[],
                        metric=None, units=None, timeframe=None, frequency=None,
                        source_ids=["rows"],
                        computation_ids=sorted((computed_numbers or {}).keys()),
                        data_points={"row_count": len(rows)},
                    ))
            except Exception:
                pass
        # Fail-closed: 0% confidence never ships a chart (graph/comparison);
        # honest row tables survive. Never 0% + visual chart.
        try:
            _zero = float(output.confidence or 0.0) <= 0.0
        except (TypeError, ValueError):
            _zero = True
        if _zero:
            synthesized = [v for v in synthesized if getattr(v, "visual_type", "") not in ("graph", "comparison")]
        if chart_requested:
            logger.info("Chart intent detected; synthesized visuals lead with a chart.")
    elif combined_series:
        graph = _market_graph_visual(combined_series, query)
        if graph is not None:
            try:
                if getattr(graph, "provenance", None) is None:
                    _ents = [str(i.get("entity", "")) for i in combined_series if isinstance(i, dict)]
                    attach_provenance(graph, build_visual_provenance(
                        query=query, entities=_ents[:4], metric=None,
                        units=None, timeframe=None, frequency=None,
                        source_ids=[f"market:{e}" for e in _ents[:4]],
                        computation_ids=[], data_points={"series": len(_ents)},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized = [graph]
                else:
                    synthesized = []
            except (TypeError, ValueError):
                synthesized = [graph]
        fundamentals_comparison = _fundamentals_comparison_visual(fundamentals, query)
        if fundamentals_comparison is not None:
            try:
                if getattr(fundamentals_comparison, "provenance", None) is None:
                    _ents = [str(i.get("entity", "")) for i in (fundamentals or []) if isinstance(i, dict)]
                    attach_provenance(fundamentals_comparison, build_visual_provenance(
                        query=query, entities=_ents[:4], metric="market_cap",
                        units=None, timeframe=None, frequency=None,
                        source_ids=[f"fundamentals:{e}" for e in _ents[:4]],
                        computation_ids=[], data_points={"entities": _ents[:4]},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(fundamentals_comparison)
            except (TypeError, ValueError):
                synthesized.append(fundamentals_comparison)
        if not synthesized:
            # Gated short-term series (historical query, single entity):
            # fall through to honest sources only, never an empty naked answer.
            synthesized = _sources_only()
    else:
        figures = _figures_from_snippets(news_context, query)
        # Explicit X-vs-Y steers to a comparison card first (never invented).
        comparison = _comparison_from_figures(figures, query)
        if comparison is not None:
            try:
                if getattr(comparison, "provenance", None) is None:
                    _fig_ents = sorted({str(f.get("entity", "")) for f in figures if f.get("entity")})[:4]
                    attach_provenance(comparison, build_visual_provenance(
                        query=query, entities=_fig_ents,
                        metric=str((figures[0].get("metric") if figures else "") or ""),
                        units=str((figures[0].get("unit") if figures else "") or ""),
                        timeframe=None, frequency=None,
                        source_ids=[f"snippet:{f.get('ref')}" for f in figures[:4]],
                        computation_ids=[], data_points={"figures": len(figures)},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(comparison)
            except (TypeError, ValueError):
                synthesized.append(comparison)
        # A figures bar over mismatched metrics (funding vs cost) is the
        # same false-comparison bug: only chart when cues agree.
        # Fail-closed: metric check unavailable blocks the bar.
        bar_ok = True
        try:
            if figures_share_metric is not None and len(figures) >= 2:
                bar_ok, _ = figures_share_metric(figures)
        except Exception as exc:
            logger.warning("Figures metric check failed, blocking bar: %s", exc)
            bar_ok = False
        if bar_ok:
            bar = _figures_bar_visual(figures, query)
            if bar is not None:
                try:
                    if getattr(bar, "provenance", None) is None:
                        _fig_ents = sorted({str(f.get("entity", "")) for f in figures if f.get("entity")})[:4]
                        attach_provenance(bar, build_visual_provenance(
                            query=query, entities=_fig_ents,
                            metric=str((figures[0].get("metric") if figures else "") or ""),
                            units=str((figures[0].get("unit") if figures else "") or ""),
                            timeframe=None, frequency=None,
                            source_ids=[f"snippet:{f.get('ref')}" for f in figures[:4]],
                            computation_ids=[], data_points={"figures": len(figures)},
                        ))
                except Exception:
                    pass
                try:
                    if float(output.confidence or 0.0) > 0.0:
                        synthesized.append(bar)
                except (TypeError, ValueError):
                    synthesized.append(bar)
        table = _figures_table_visual(figures)
        if table is not None:
            # Data tables carry provenance like charts (P0#14).
            try:
                if getattr(table, "provenance", None) is None:
                    _fig_ents = sorted({str(f.get("entity", "")) for f in figures if f.get("entity")})[:4]
                    attach_provenance(table, build_visual_provenance(
                        query=query, entities=_fig_ents,
                        metric=str((figures[0].get("metric") if figures else "") or "") or None,
                        units=str((figures[0].get("unit") if figures else "") or "") or None,
                        timeframe=None, frequency=None,
                        source_ids=[f"snippet:{f.get('ref')}" for f in figures[:4]],
                        computation_ids=[], data_points={"figures": len(figures)},
                    ))
            except Exception:
                pass
            synthesized.append(table)
        timeline = _timeline_visual(news_context, web_sources)
        if timeline is not None:
            synthesized.append(timeline)
        # Structured fundamentals comparison needs no snippet figures.
        fundamentals_comparison = _fundamentals_comparison_visual(fundamentals, query)
        if fundamentals_comparison is not None:
            try:
                if getattr(fundamentals_comparison, "provenance", None) is None:
                    _fents = [str(i.get("entity", "")) for i in (fundamentals or []) if isinstance(i, dict)]
                    attach_provenance(fundamentals_comparison, build_visual_provenance(
                        query=query, entities=_fents[:4], metric="market_cap",
                        units=None, timeframe=None, frequency=None,
                        source_ids=[f"fundamentals:{e}" for e in _fents[:4]],
                        computation_ids=[], data_points={"entities": _fents[:4]},
                    ))
            except Exception:
                pass
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(fundamentals_comparison)
            except (TypeError, ValueError):
                synthesized.append(fundamentals_comparison)
        outlook = _outlook_status_visual(news_context, query)
        if outlook is not None:
            synthesized.append(outlook)
        sources = _sources_table_visual(web_sources)
        if sources is not None:
            synthesized.append(sources)
    if synthesized:
        logger.info(f"Visual guarantee synthesized {len(synthesized)} visual(s).")
        output.visuals = list(output.visuals) + synthesized[:3]
    # Final provenance gate for all synthesized paths (fail closed).
    try:
        output.visuals = _provenance_filter_final(output.visuals, query, gate)
    except Exception as exc:
        logger.warning("Final provenance filter failed: %s", exc)
        output.visuals = [v for v in (output.visuals or []) if getattr(v, "visual_type", "") not in ("graph", "comparison")]
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
    on_stage: Optional[Callable[[str], Awaitable[None]]] = None,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    macro_note: Optional[str] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    research_notes: Optional[list] = None,
    prior_research_state: Optional[Dict[str, Any]] = None,
    plan_query: Optional[str] = None,
) -> PipelineOutput:
    """Decision-driven pipeline: judge -> narrate -> ground -> guarantee (specs/06).

    1. The sufficiency judge (LLM) decides answer-vs-clarify and plans visuals
       from the actual tool outputs on hand.
    2. The narration call answers following that verdict, with prior-turn
       context so follow-ups resolve instead of looping. Live-web narration
       routes through the stronger model (specs/12 synthesis touchpoint).
    3. LLM-proposed web visuals are grounding-checked: any numeric visual
       whose numbers don't appear in the cited snippets is discarded for the
       deterministic figures fallback (checked trust, not implicit).
    4. The deterministic visual guarantee fills visuals from real rows/series
       when a validated answer arrives naked.
    5. A repeat-clarification backstop re-asks the narrator once with
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
    if fundamentals is None:
        fundamentals = []
    if macro_data is None:
        macro_data = []
    if macro_note is None:
        macro_note = ""
    if price_history is None:
        price_history = []
    if financial_history is None:
        financial_history = []
    if research_notes is None:
        research_notes = []

    rows = list(db_data or [])
    thinking: List[str] = []
    # Deterministic what-if (generic): structured-history scenario first,
    # then the row-level price scenario for own-data queries. Both compute
    # in code; narration quotes verbatim (never LLM arithmetic).
    try:
        if is_what_if_query(plan_query or user_query) and not (computed_numbers or {}).get("what_if"):
            _structured = compute_structured_what_if(
                financial_history, plan_query or user_query
            )
            if _structured is not None:
                computed_numbers = {**(computed_numbers or {}), "what_if": _structured}
                thinking.append(
                    f"Deterministic what-if computed: {str(_structured.get('pct_change'))}% "
                    f"(factor {str(_structured.get('factor'))})."
                )
            elif rows:
                try:
                    from app.services.data.stats import (
                        apply_what_if as _apply_wif,
                        parse_what_if as _parse_wif,
                    )

                    _scenario = _parse_wif(plan_query or user_query)
                    if _scenario is not None:
                        _row_wif = _apply_wif(rows, *_scenario)
                        if _row_wif is not None:
                            computed_numbers = {**(computed_numbers or {}), "what_if": _row_wif}
                            thinking.append(
                                f"Deterministic row what-if computed: "
                                f"{str(_row_wif.get('pct_change'))}%."
                            )
                except Exception as _exc:
                    logger.warning("Row what-if skipped: %s", _exc)
            if not (computed_numbers or {}).get("what_if"):
                # No uploaded data at all (or it didn't yield a scenario):
                # the question may state its own baseline numbers directly
                # ("5,000 customers paying $100/month. If price +25%...").
                # Compute that deterministically too -- never let the LLM
                # do this arithmetic silently just because there's no data
                # table to scale from.
                try:
                    from app.services.data.stats import (
                        compute_freeform_scenario as _compute_freeform,
                    )

                    _freeform = _compute_freeform(plan_query or user_query)
                    if _freeform is not None:
                        computed_numbers = {**(computed_numbers or {}), "what_if": _freeform}
                        thinking.append(
                            f"Deterministic freeform scenario computed: "
                            f"{str(_freeform.get('pct_change'))}% revenue change "
                            f"from stated baseline (no uploaded data used)."
                        )
                except Exception as _exc:
                    logger.warning("Freeform scenario skipped: %s", _exc)
    except Exception as exc:
        logger.warning("Structured what-if skipped: %s", exc)
    for note in research_notes:
        thinking.append(f"Research: {str(note)[:200]}")

    async def emit(stage: str) -> None:
        if on_stage is not None:
            try:
                await on_stage(stage)
            except Exception as exc:
                logger.warning(f"Stage callback failed: {exc}")

    async def _rescue_or_fallback(reason: str) -> PipelineOutput:
        """Prose rescue over real evidence, else the honest generic fallback."""
        rescued = await _narrate_prose_rescue(
            user_query, rows, news_context, market_data
        )
        if rescued is not None:
            rescued.thinking = thinking + ["Structured narration failed; prose rescue."]
            return ensure_visuals(
                rescued,
                rows=rows,
                computed_numbers=computed_numbers,
                market_data=market_data,
                web_sources=web_sources,
                news_context=news_context,
                preferred_visual=None,
                query=user_query,
                fundamentals=fundamentals,
                macro_data=macro_data,
                price_history=price_history,
                financial_history=financial_history,
            )
        return fallback_output(reason=reason, confidence=0.0)

    # The canonical ResearchPlan is the single source of truth for this
    # request (Phase 1): decomposition -> entities/metrics/time-range ->
    # required tools. Every downstream stage (judge evidence, gate,
    # confidence, visuals, research_state) reads from it -- never from a
    # competing ad-hoc decomposition. plan_query carries the ORIGINAL
    # research intent when this turn answers a clarification (Phase 3);
    # narration still uses the user's literal message.
    plan_dict: Dict[str, Any] = {}
    plan_text = plan_query or user_query
    try:
        if build_research_plan is not None:
            plan_dict = dict(build_research_plan(plan_text, source_scope=source_scope) or {})
    except Exception as exc:
        logger.warning("Canonical research plan failed: %s", exc)
        plan_dict = {}
    # Prior research reuse is explicit + validated (P0#20): when the caller
    # passes the previous turn's structured state, compare its canonical
    # structure with the current plan. A structural match records reuse;
    # any entity/metric/timeframe change records non-reuse (fresh evidence
    # only). Research is still recomputed from current retrieval -- this
    # never injects stale rows, it only documents continuity honestly.
    try:
        if isinstance(prior_research_state, dict):
            _prior_plan = (prior_research_state.get("plan") or {})
            _prior_ents = {str(e).strip().lower() for e in (_prior_plan.get("entities", []) or [])}
            _cur_ents = {str(e).strip().lower() for e in (plan_dict.get("entities", []) or [])}
            _prior_mets = {str(m).strip().lower() for m in (_prior_plan.get("metrics", []) or [])}
            _cur_mets = {str(m).strip().lower() for m in (plan_dict.get("metrics", []) or [])}
            _prior_tf = str((_prior_plan.get("time_range", {}) or {}).get("label", "") or "")
            _cur_tf = str((plan_dict.get("time_range", {}) or {}).get("label", "") or "")
            if _prior_ents == _cur_ents and _prior_mets == _cur_mets and _prior_tf == _cur_tf:
                thinking.append("Prior research structure matches; continuity validated.")
            else:
                thinking.append("Prior research structure differs; using fresh evidence only.")
    except Exception as exc:
        logger.warning("Prior-research reuse check failed: %s", exc)
    entities_found: list = []

    # Deterministic historical gate + stats BEFORE the LLM narrates, so the
    # model can only narrate validated numbers (specs/11 S2) and the prompt
    # carries an explicit BLOCKED/PASSED verdict.
    gate: Dict[str, Any] = {}
    try:
        gate = _historical_comparison_gate(
            plan_text,
            market_data=market_data,
            price_history=price_history,
            financial_history=financial_history,
            fundamentals=fundamentals,
        )
    except Exception as exc:
        # Fail-closed: gate unavailable -> BLOCK comparison/chart.
        logger.warning("Historical gate failed: blocking comparison: %s", exc)
        gate = _fail_closed_gate(plan_text, f"historical validation unavailable ({exc})")
        thinking.append("Historical validation unavailable; comparison blocked.")
    # What-if intent never consumes market-history stats (no inheritance):
    # scenario math comes from deterministic what-if computation only.
    if gate.get("comparison_stats") and not is_what_if_query(plan_text):
        computed_numbers = {
            **(computed_numbers or {}),
            "comparison_stats": gate["comparison_stats"],
        }
    elif gate.get("comparison_stats") and is_what_if_query(plan_text):
        thinking.append("What-if query: historical stats withheld (scenario math only).")
    if gate.get("applies") and gate.get("blocked"):
        thinking.append(f"Historical gate BLOCKED: {(gate.get('blocked_reason', '') or '')[:160]}")
    if gate.get("partial"):
        thinking.append(f"Partial evidence: {gate.get('exclusion_note', '')[:160]}")

    # Canonical query semantics: plan_text (the merged clarification-aware
    # research intent) is the single source of truth for research AND
    # narration. The fragmentary user_query is kept only for display/trace.
    # Using the fragment for narration while the gate used the merged plan
    # let period/metric diverge (e.g. gate PASSED 3Y while the prompt asked
    # 1Y). Every stage below reads plan_text.
    try:
        await emit("judging")
        decision = await judge_sufficiency(
            user_query=plan_text,
            source_scope=source_scope,
            evidence=_evidence_inventory(
                rows,
                computed_numbers,
                news_context,
                market_data,
                fundamentals=fundamentals,
                macro_data=macro_data,
                price_history=price_history,
                financial_history=financial_history,
            ),
            prior_clarification=prior_clarification,
            prior_data=prior_data,
        )
        # An explicitly requested shape in the canonical query wins over the judge's.
        detected = detect_preferred_visual(plan_text)
        if detected is not None:
            decision.preferred_visual = detected
        # Phase 3 contract: publicly researchable information must never
        # require the user to supply it. When the query is a researchable
        # comparison (arbitrary entities + metrics detected), clarification
        # is DETERMINISTICALLY forbidden -- the judge's "clarify" is
        # overridden here in code, not by prompt compliance. Genuine
        # ambiguity (no entities/metrics) may still clarify. Generic
        # discipline: never ask for already-supplied or safely defaultable
        # info (timeframes, geographies, currencies, source prefs).
        try:
            if decision.decision == "clarify" and must_not_clarify is not None:
                if must_not_clarify(plan_text):
                    logger.warning(
                        "Deterministic clarification ban: researchable "
                        "comparison must be answered from evidence, not asked."
                    )
                    thinking.append(
                        "Clarification forbidden by deterministic rule "
                        "(researchable public-company comparison); "
                        "answering from researched evidence."
                    )
                    decision = Decision(
                        decision="answer",
                        missing="",
                        chart_from_prior=decision.chart_from_prior,
                        visual_plan=decision.visual_plan,
                        suggested_options=[],
                        preferred_visual=decision.preferred_visual,
                        tools_needed=decision.tools_needed,
                    )
                elif decision.missing:
                    try:
                        from app.services.data.comparison import (
                            clarification_is_redundant as _redundant,
                        )

                        redundant, why = _redundant(decision.missing, plan_text)
                    except Exception:
                        redundant, why = False, ""
                    if redundant:
                        logger.warning(
                            "Redundant clarification suppressed (%s); answering.", why
                        )
                        thinking.append(
                            f"Redundant clarification suppressed ({why}); answering."
                        )
                        decision = Decision(
                            decision="answer",
                            missing="",
                            chart_from_prior=decision.chart_from_prior,
                            visual_plan=decision.visual_plan,
                            suggested_options=[],
                            preferred_visual=decision.preferred_visual,
                            tools_needed=decision.tools_needed,
                        )
        except Exception as exc:
            logger.warning("Clarification-ban check failed: %s", exc)
        logger.info(
            f"Pipeline decision: {decision.decision} "
            f"(chart_from_prior={decision.chart_from_prior}, "
            f"preferred={decision.preferred_visual}, "
            f"plan={[item.kind for item in decision.visual_plan]})"
        )
        thinking.append(
            f"Judged: {decision.decision} "
            f"({len(rows)} row(s), {len(news_context)} snippet(s), "
            f"{len(market_data)} market series, "
            f"{len(price_history)} price-history, "
            f"{len(financial_history)} financial-history)"
        )

        narrate_rows = rows
        # Follow-up staleness: prior rows are reused ONLY when the current
        # request matches the prior entities/metrics/period. A changed
        # entity/metric/period or a what-if query regenerates from current
        # evidence, never stale data. A pure presentation change
        # (qualitative -> chart, "chart that") explicitly reuses prior rows
        # and regenerates visuals -- intent change alone is not stale there.
        _prior_query = str((prior_data or {}).get("from_query", "") or "")
        try:
            # Canonical staleness (P0#19): the single is_visual_stale_for_query
            # verdict drives prior-data reuse -- no divergent inline copy.
            # It covers entity/metric/period/what-if/intent; frequency/unit/
            # currency/source-identity changes additionally force staleness
            # below via validated-state comparison where available.
            _stale, _why = is_visual_stale_for_query(
                VisualOutput(visual_type="status", props={}, title="stale-probe"),
                plan_text, _prior_query,
            ) if _prior_query else (False, "")
            # is_visual_stale_for_query treats a pure chart presentation
            # follow-up ("chart that") as NOT stale -- honor that here.
            # Its probe visual carries no entities so only structural
            # query comparison applies (exactly what prior reuse needs).
        except Exception:
            _stale, _why = False, ""
        _what_if_now = is_what_if_query(plan_text)
        if decision.chart_from_prior and prior_data and not rows:
            # Follow-up on the previous answer ("chart that"): narrate from
            # the prior rows so the request resolves instead of clarifying --
            # unless stale (regenerate) or what-if (never reuse history).
            if _stale or _what_if_now:
                logger.info(
                    "Prior-data reuse blocked (stale=%s, what_if=%s: %s); "
                    "regenerating from current evidence.", _stale, _what_if_now, _why,
                )
                thinking.append("Prior data stale for this follow-up; using current evidence.")
                narrate_rows = rows
            else:
                narrate_rows = prior_data.get("rows", []) or []
        if not narrate_rows and prior_data:
            # Deterministic backstop (no judge needed): an explicit chart
            # request with no fresh rows but a prior answer's rows always
            # resolves from the prior rows -- unless stale/what-if.
            if re.search(CHART_INTENT_RE, plan_text, re.IGNORECASE) and not _stale and not _what_if_now:
                logger.info("Chart follow-up resolved from prior answer rows.")
                narrate_rows = prior_data.get("rows", []) or []

        await emit("narrating")
        output = await _narrate(
            user_query=plan_text,
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
            web_sources=web_sources,
            fundamentals=fundamentals,
            macro_data=macro_data,
            macro_note=macro_note,
            price_history=price_history,
            financial_history=financial_history,
            comparison_gate=gate,
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
                user_query=plan_text,
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
                web_sources=web_sources,
                fundamentals=fundamentals,
                macro_data=macro_data,
                macro_note=macro_note,
                price_history=price_history,
                financial_history=financial_history,
                comparison_gate=gate,
            )

        if output.clarification is not None:
            # Hard guard against the ask-user-for-data loop (seen live:
            # "Please provide annual revenue, net income, and profit margin
            # figures..."): public company statistics are researchable, so
            # a clarification requesting them is discarded and the pipeline
            # answers from researched evidence (or states what is missing)
            # instead of stalling or restarting on the user's reply.
            # Widened (Phase 3): ANY clarification on a deterministically
            # researchable comparison is suppressed, not just ones matching
            # the ask-for-data pattern -- the narrator must not re-open a
            # question the deterministic layer already closed.
            try:
                asks_data = (
                    clarification_asks_for_researchable_data(
                        output.clarification.question, plan_text
                    )
                    if clarification_asks_for_researchable_data is not None
                    else False
                )
            except Exception:
                asks_data = False
            try:
                banned = bool(
                    must_not_clarify is not None and must_not_clarify(plan_text)
                )
            except Exception:
                banned = False
            try:
                from app.services.data.comparison import (
                    clarification_is_redundant as _redundant2,
                )

                redundant2, _why2 = _redundant2(
                    output.clarification.question, plan_text
                )
            except Exception:
                redundant2, _why2 = False, ""
            if asks_data or banned or redundant2:
                logger.warning(
                    "Researchable-data clarification suppressed; "
                    "answering from researched evidence."
                )
                thinking.append(
                    "Suppressed ask-user-for-data clarification; "
                    "answered from researched evidence."
                )
                output = await _narrate(
                    user_query=plan_text,
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
                    web_sources=web_sources,
                    fundamentals=fundamentals,
                    macro_data=macro_data,
                    macro_note=macro_note,
                    price_history=price_history,
                    financial_history=financial_history,
                    comparison_gate=gate,
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
            # Phase 13 contract (hard gate, enforced in CODE, never by
            # prompt): when the historical gate BLOCKED, the narrator must
            # not ship comparison visuals. Strip any LLM-fabricated graph /
            # comparison cards here -- grounding alone cannot catch a chart
            # whose numbers happen to appear in the snippets but whose
            # comparison the gate rejected.
            try:
                if gate.get("applies") and gate.get("blocked") and output.visuals:
                    before = len(output.visuals)
                    output.visuals = [
                        visual for visual in output.visuals
                        if getattr(visual, "visual_type", "") not in ("graph", "comparison")
                    ]
                    stripped = before - len(output.visuals)
                    if stripped:
                        logger.info(
                            "Stripped %d fabricated comparison visual(s): gate BLOCKED.",
                            stripped,
                        )
                        thinking.append(
                            f"Stripped {stripped} comparison visual(s): "
                            "historical gate BLOCKED."
                        )
                # Fail-closed: zero confidence never ships a chart, even when
                # the gate passed (e.g. validator threw mid-flight and the
                # gate defaulted). Tables/sources prose visuals survive.
                try:
                    zero_conf = float(output.confidence or 0.0) <= 0.0
                except (TypeError, ValueError):
                    zero_conf = True
                if zero_conf and output.visuals:
                    before = len(output.visuals)
                    output.visuals = [
                        visual for visual in output.visuals
                        if getattr(visual, "visual_type", "") not in ("graph", "comparison")
                    ]
                    stripped = before - len(output.visuals)
                    if stripped:
                        logger.info(
                            "Stripped %d chart visual(s): zero confidence.",
                            stripped,
                        )
                        thinking.append(
                            f"Stripped {stripped} chart visual(s): zero confidence."
                        )
            except Exception as exc:
                # Fail-closed: stripping itself failed -> drop all chart
                # visuals rather than risk shipping an unverified one.
                logger.warning("Visual strip failed, dropping charts: %s", exc)
                try:
                    output.visuals = [
                        visual for visual in (output.visuals or [])
                        if getattr(visual, "visual_type", "") not in ("graph", "comparison")
                    ]
                except Exception:
                    output.visuals = []
            # Checked trust (P0#13 uniform): LLM-proposed numeric visuals
            # must ground in validated evidence/computation for EVERY visual
            # type and channel -- no row-presence exemption. Ungrounded ones
            # fall back to the deterministic figures path via ensure_visuals.
            if output.visuals and (
                news_context or narrate_rows or price_history
                or financial_history or market_data or computed_numbers
            ):
                kept, dropped = drop_ungrounded_visuals_evidence(
                    list(output.visuals), snippets=news_context,
                    rows=narrate_rows, price_history=price_history,
                    financial_history=financial_history,
                    market_data=market_data,
                    computed_numbers=computed_numbers,
                )
                if dropped:
                    thinking.append(
                        f"Dropped {dropped} ungrounded visual(s); "
                        "deterministic fallback applies."
                    )
                    output.visuals = kept
            # Decision.tools_needed is advisory post-research, but it is no
            # longer dead: tools the judge asked for with no evidence on hand
            # cap confidence and are recorded instead of silently ignored.
            try:
                if reconcile_judge_tools is not None and decision.tools_needed:
                    reconciled = reconcile_judge_tools(
                        decision.tools_needed,
                        _evidence_inventory(
                            narrate_rows, computed_numbers, news_context,
                            market_data, fundamentals=fundamentals,
                            macro_data=macro_data, price_history=price_history,
                            financial_history=financial_history,
                        ),
                    )
                    if reconciled.get("missing"):
                        logger.info(
                            "Judge-requested tools lacking evidence: %s",
                            reconciled["missing"],
                        )
                        thinking.append(
                            "Judge-requested tools lacking evidence: "
                            + ", ".join(reconciled["missing"])
                        )
                        output.confidence = min(float(output.confidence or 0.0), 0.65)
            except Exception as exc:
                logger.warning("Judge-tools reconcile failed: %s", exc)
            plan = [item.kind for item in decision.visual_plan]
            thinking.append(
                f"Answered from {len(narrate_rows)} row(s) + "
                f"{len(news_context)} snippet(s)"
                + (f"; planned visuals: {', '.join(plan)}" if plan else "")
            )
            providers = {
                str(source.get("provider", "")).strip().lower()
                for source in (web_sources or [])
                if str(source.get("provider", "")).strip()
            }
            # Single validated-evidence state: answer scope, stats,
            # confidence, visuals, and narration all read the same canonical
            # validated subset (never independent existence checks).
            try:
                _completeness_for_state = None
                if check_research_completeness is not None and plan_dict:
                    try:
                        _completeness_for_state = check_research_completeness(
                            plan_dict,
                            {
                                "price_history": price_history,
                                "financial_history": financial_history,
                                "market_data": market_data,
                                "fundamentals": fundamentals,
                                "snippets": news_context,
                            },
                        ) or {}
                    except Exception:
                        _completeness_for_state = None
                validated_state = build_validated_evidence_state(
                    query=plan_text, plan=plan_dict, gate=gate,
                    completeness=_completeness_for_state,
                )
            except Exception as exc:
                logger.warning("Validated-evidence state failed: %s", exc)
                validated_state = {
                    "validated_entities": [], "validated_metrics": [],
                    "sufficient": False, "partial": False, "blocked": bool(gate.get("blocked")),
                    "comparison_stats": gate.get("comparison_stats"),
                    "exclusion_note": str(gate.get("exclusion_note", "") or ""),
                }
            # Partial exclusion transparency (P0#5): state excluded
            # entities/metrics in prose exactly once per response, tracked
            # structurally (exclusion_disclosed flag), never by fragile
            # exact-string matching as the source of truth.
            try:
                from app.services.data.canonical import (
                    exclusion_note_for as _excl_for,
                )

                _excl_note = str(validated_state.get("exclusion_note", "") or "")
                if not _excl_note and validated_state.get("partial"):
                    _excl_note = _excl_for(validated_state)
                # What-if answers are scenario results, not comparisons: a
                # computed scenario suppresses comparison-metric exclusion
                # notes (the assumption + projected result are the evidence).
                try:
                    if is_what_if_query(plan_text) and (computed_numbers or {}).get("what_if"):
                        _excl_note = ""
                except Exception:
                    pass
                if _excl_note and output.clarification is None and not validated_state.get(
                    "exclusion_disclosed"
                ):
                    output.answer = str(output.answer or "").rstrip() + "\n\n" + _excl_note
                    try:
                        validated_state["exclusion_disclosed"] = True
                        validated_state["exclusion_note"] = _excl_note
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Exclusion-note append failed: %s", exc)
            # Explicit evidence requirements (P0#3): entities x metrics x
            # timeframe x data type, derived BEFORE reading retrieval results;
            # fulfilled/missing recorded in the trace (never re-requested).
            try:
                from app.services.data.canonical import (
                    derive_evidence_requirements as _derive_reqs,
                    reconcile_requirements as _reconcile_reqs,
                )

                _reqs = _derive_reqs(
                    list(plan_dict.get("entities", []) or []),
                    list(plan_dict.get("metrics", []) or []),
                    plan_dict.get("period_label"),
                )
                _per_ok: dict = {}
                try:
                    _per_ok = (_completeness_for_state or {}).get("per_entity", {}) or {}
                except Exception:
                    _per_ok = {}
                _rec = _reconcile_reqs(_reqs, _per_ok)
                if _rec.get("missing"):
                    thinking.append(
                        "Evidence requirements missing: "
                        + "; ".join(
                            f"{m['entity']}/{m['metric']}" for m in _rec["missing"][:6]
                        )
                    )
            except Exception as exc:
                logger.warning("Requirement reconcile failed: %s", exc)
            # Source-quality signal (P1#29): provider diversity + structured
            # provenance tiers recorded (authoritative filings/structured >
            # snippets). Count alone never decides quality.
            try:
                _provs = sorted(providers or [])
                if _provs:
                    thinking.append(f"Evidence providers: {', '.join(_provs[:4])}")
            except Exception:
                pass
            # Phase 16 contract: confidence is evidence-driven. The canonical
            # validated state produces a deterministic cap (BLOCKED -> 0,
            # insufficient -> 0, partial -> capped low); the LLM's subjective
            # number survives below it, never above.
            entities_found = sorted(set(
                list(validated_state.get("validated_entities", []) or [])
                or list(gate.get("validated_entities", []) or [])
            ))
            # Fall back to raw-found only when no validated subset exists
            # (non-comparison qualitative answers).
            if not entities_found and not gate.get("applies"):
                entities_found = sorted({
                    str(item.get("entity", "") or item.get("symbol", ""))
                    for lst in (list(price_history) + list(financial_history) + list(market_data))
                    for item in [lst if isinstance(lst, dict) else {}]
                    if isinstance(lst, dict) and (lst.get("values") or (lst.get("revenue", {}) or {}).get("values"))
                })
            metrics_found = list(
                validated_state.get("validated_metrics", [])
                or gate.get("validated_metrics", [])
                or (list(plan_dict.get("metrics", []) or []) if gate.get("applies") and not gate.get("blocked") else [])
            )
            try:
                if evidence_driven_confidence is not None and (plan_dict.get("is_comparison") or gate.get("applies")):
                    capped = evidence_driven_confidence(
                        output.confidence,
                        plan=plan_dict,
                        entities_found=entities_found,
                        metrics_found=metrics_found,
                        historical_ok=bool(gate.get("historical_ok", True)),
                        comparison_ok=bool(gate.get("comparison_ok", True)),
                        row_count=len(narrate_rows),
                        source_count=len(web_sources or []),
                        snippet_count=len(news_context),
                        provider_count=len(providers),
                        has_structured=bool(market_data or macro_data or fundamentals or price_history or financial_history),
                    )
                else:
                    capped = evidence_confidence_cap(
                        output.confidence,
                        row_count=len(narrate_rows),
                        snippet_count=len(news_context),
                        provider_count=len(providers),
                        has_market=bool(market_data or macro_data or fundamentals),
                    )
            except Exception as exc:
                logger.warning("Evidence-driven confidence failed: %s", exc)
                capped = evidence_confidence_cap(
                    output.confidence,
                    row_count=len(narrate_rows),
                    snippet_count=len(news_context),
                    provider_count=len(providers),
                    has_market=bool(market_data or macro_data or fundamentals),
                )
            # Historical gate overrides the generic cap: BLOCKED means 0.0
            # (insufficient evidence), PASSED keeps the generic cap. Partial
            # caps at 0.65 (never full 0.85). A short-term-only series must
            # never inflate confidence for a multi-year ask.
            if gate.get("applies"):
                if gate.get("blocked"):
                    if output.confidence != 0.0:
                        logger.info(
                            "Confidence forced to 0.0 by historical gate: %s",
                            (gate.get("blocked_reason", "") or "")[:160],
                        )
                        thinking.append("Confidence forced to 0.0: historical evidence insufficient.")
                    output.confidence = 0.0
                    capped = 0.0
                elif gate.get("partial"):
                    capped = min(capped, 0.65)
                    if output.confidence > capped + 0.005:
                        thinking.append(
                            f"Confidence capped by partial evidence: {output.confidence} -> {capped}"
                        )
                        output.confidence = capped
                else:
                    # PASSED gate keeps the evidence-driven cap as-is (P0#24):
                    # confidence represents evidence quality/coverage, and no
                    # PASSED verdict may lift it regardless of quality. A
                    # sparse two-point series earns its thin-evidence cap, not
                    # an automatic >=0.65.
                    pass
            # Sparse-series penalty (P0#24/P1#29): a validated gate over thin
            # observations (2 points for a multi-year ask) or a single
            # provider caps below full confidence; source count alone never
            # makes weak evidence look strong.
            try:
                _years = gate.get("years") if gate.get("applies") else None
                if _years and int(_years) >= 3 and not gate.get("blocked"):
                    _min_obs = 10**9
                    for _lst in (list(price_history or []) + list(market_data or [])):
                        if isinstance(_lst, dict) and _lst.get("values"):
                            _min_obs = min(_min_obs, len(list(_lst.get("values") or [])))
                    for _item in list(financial_history or []):
                        if isinstance(_item, dict):
                            for _bk in ("revenue", "net_income"):
                                _chunk = (_item.get(_bk, {}) or {})
                                if isinstance(_chunk, dict) and _chunk.get("values"):
                                    _min_obs = min(_min_obs, len(list(_chunk.get("values") or [])))
                    if _min_obs <= 2:
                        capped = min(capped, 0.45)
                        thinking.append(
                            "Confidence capped by sparse observations "
                            f"(min_obs={_min_obs} for {_years}Y)."
                        )
                    if len(providers) <= 1 and not (price_history or financial_history):
                        capped = min(capped, 0.65)
            except Exception:
                pass
            if capped < output.confidence - 0.005:
                logger.info(
                    f"Confidence capped by evidence: {output.confidence} -> {capped}."
                )
                thinking.append(
                    f"Confidence capped by evidence: {output.confidence} -> {capped}"
                )
                output.confidence = capped

        await emit("visuals")
        output = ensure_visuals(
            output,
            rows=narrate_rows,
            computed_numbers=computed_numbers,
            market_data=market_data,
            web_sources=web_sources,
            news_context=news_context,
            preferred_visual=decision.preferred_visual,
            query=plan_text,
            fundamentals=fundamentals,
            macro_data=macro_data,
            price_history=price_history,
            financial_history=financial_history,
        )
        if output.visuals:
            thinking.append(
                f"Visuals out: {', '.join(v.visual_type for v in output.visuals)}"
            )
        # Deterministic visual-plan reconciliation (P0#18): the judge's
        # visual_plan is advisory; the deterministic planner below is
        # authoritative. Divergence is logged (never silent) and the
        # deterministic verdict wins -- there is exactly one effective plan.
        try:
            _det_plan = plan_visuals_from_evidence(
                query=plan_text,
                validated_state=validated_state,
                has_rows=bool(narrate_rows),
                has_history=bool(price_history or financial_history),
                has_market=bool(market_data),
                has_snippets=bool(news_context),
            )
            _judge_plan = [item.kind for item in (decision.visual_plan or [])]
            if set(_judge_plan) != set(_det_plan):
                logger.info(
                    "Visual plan reconciled: judge=%s deterministic=%s.",
                    _judge_plan, _det_plan,
                )
                thinking.append(
                    f"Visual plan reconciled (deterministic wins): {', '.join(_det_plan) or 'none'}"
                )
        except Exception as exc:
            logger.warning("Visual-plan reconcile failed: %s", exc)
        # Code-grounded narration + final validation (P0#22/#23): claims
        # exceeding validated evidence are removed/blocked in code, never by
        # prompt compliance alone. Follow-ups are grounded (P1#30) and the
        # what-if assumption is verified verbatim (P0#10).
        try:
            output = apply_narration_contract(
                output,
                validated_state=validated_state,
                computed_numbers=computed_numbers,
                gate=gate,
                thinking=thinking,
            )
        except Exception as exc:
            logger.warning("Narration contract failed (fail-open prose kept): %s", exc)
        # Phase 18: compact structured research state for follow-ups (never
        # raw payloads). Phase 19: structured trace log (no secrets/PII).
        try:
            output.research_state = {
                "query": user_query[:300],
                "plan": {
                    "entities": list(plan_dict.get("entities", []) or []),
                    "metrics": list(plan_dict.get("metrics", []) or []),
                    "time_range": plan_dict.get("time_range"),
                    "requires_history": bool(plan_dict.get("requires_history")),
                    "required_tools": list(plan_dict.get("required_tools", []) or []),
                },
                "entities_found": entities_found if output.clarification is None else [],
                "gate": {
                    "applies": bool(gate.get("applies")),
                    "blocked": bool(gate.get("blocked")),
                    "blocked_reason": str(gate.get("blocked_reason", "") or "")[:300],
                },
                "confidence": output.confidence,
                "sources": [
                    {
                        "title": str(source.get("title", ""))[:120],
                        "url": str(source.get("url", ""))[:300],
                        "provider": str(source.get("provider", ""))[:60],
                    }
                    for source in (web_sources or [])[:12]
                ],
            }
        except Exception as exc:
            logger.warning("Research-state build failed: %s", exc)
        try:
            _executed = [
                        name for name, lst in (
                            ("market", market_data), ("macro", macro_data),
                            ("fundamentals", fundamentals),
                            ("price_history", price_history),
                            ("financial_history", financial_history),
                        ) if lst
                    ] + (["snippets"] if news_context else [])
            _completeness: Dict[str, Any] = {}
            _missing_entities: list = []
            _missing_metrics: list = []
            try:
                if check_research_completeness is not None and plan_dict:
                    _completeness = check_research_completeness(
                        plan_dict,
                        {
                            "price_history": price_history,
                            "financial_history": financial_history,
                            "market_data": market_data,
                            "fundamentals": fundamentals,
                            "snippets": news_context,
                        },
                    ) or {}
                    _missing_entities = list(_completeness.get("missing_entities", []) or [])
                    _missing_metrics = list(_completeness.get("missing_metrics", []) or [])
            except Exception as exc:
                logger.warning("Completeness for trace failed: %s", exc)
            trace = (
                build_trace(
                    query=user_query,
                    plan=plan_dict,
                    tools_requested=list(plan_dict.get("required_tools", []) or []),
                    tools_executed=_executed,
                    planned_tools=list(getattr(decision, "tools_needed", []) or []),
                    actually_executed_tools=_executed,
                    tool_results={
                        "rows": len(narrate_rows),
                        "snippets": len(news_context),
                        "market_series": len(market_data or []),
                        "price_history": len(price_history or []),
                        "financial_history": len(financial_history or []),
                    },
                    missing_entities=_missing_entities,
                    missing_metrics=_missing_metrics,
                    completeness={
                        "comparison_complete": bool(_completeness.get("comparison_complete"))
                        if _completeness else (not bool(gate.get("blocked")) if gate.get("applies") else True),
                        "missing": list((_completeness.get("missing") or [])[:6]) if _completeness else [],
                    },
                    missing_evidence=(
                        [gate.get("blocked_reason", "")[:200]]
                        if gate.get("blocked") else []
                    ),
                    comparison_gate=gate,
                    calculated_stats=bool((computed_numbers or {}).get("comparison_stats")),
                    visual_decision=[v.visual_type for v in (output.visuals or [])],
                    final_confidence=output.confidence,
                    entities=list(plan_dict.get("entities", []) or []),
                    metrics=list(plan_dict.get("metrics", []) or []),
                    time_range=plan_dict.get("time_range"),
                )
                if build_trace is not None else {}
            )
            log_runtime_trace(trace)
        except Exception as exc:
            logger.warning("Trace log failed: %s", exc)
        output.thinking = thinking + list(output.thinking or [])
        return output

    except ValidationError as ve:
        logger.error(f"Schema validation failed: {ve}")
        return await _rescue_or_fallback(
            reason="Sorry, I could not process your request properly. Please try again."
        )

    except ValueError as ve:
        logger.error(f"JSON extraction failed: {ve}")
        return await _rescue_or_fallback(
            reason="I had trouble understanding the data. Please rephrase your query."
        )

    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        return await _rescue_or_fallback(
            reason="Something went wrong. Please try again."
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
    web_sources: Optional[list] = None,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    macro_note: Optional[str] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    comparison_gate: Optional[Dict[str, Any]] = None,
) -> PipelineOutput:
    """One narration call: prompt -> LLM -> validated PipelineOutput.

    Live-web synthesis routes through the stronger model (specs/12): scraped
    prose is the highest-hallucination-risk evidence, so source_scope in
    ("live_web", "both") uses groq_strong_model while own-data keeps the
    default. Unconfigured strong model == default (no behavior change).
    """
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
        web_sources=web_sources,
        fundamentals=fundamentals,
        macro_data=macro_data,
        macro_note=macro_note,
        price_history=price_history,
        financial_history=financial_history,
        comparison_gate=comparison_gate,
    )

    try:
        narration_model: Optional[str] = None
        if source_scope in ("live_web", "both"):
            narration_model = get_settings().groq_strong_model
            logger.info("Live-web narration routed to stronger model.")
    except Exception:
        narration_model = None

    result = await generate_response(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        model=narration_model,
        temperature=0.2,
        max_tokens=2000,
        json_mode=True,
    )

    raw_output = (result.get("content") or "").strip()
    if not raw_output:
        # Same transient-empty-completion rescue as the judge: one retry with
        # slightly higher temperature before giving up on this narration.
        logger.warning("Narration got empty content; retrying once.")
        result = await generate_response(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            model=narration_model,
            temperature=0.3,
            max_tokens=2000,
            json_mode=True,
        )
        raw_output = (result.get("content") or "").strip()
    logger.info(f"LLM source used: {result.get('source', 'unknown')}")

    parsed = normalize_pipeline_payload(extract_json(raw_output))

    return PipelineOutput(**parsed)


PROSE_RESCUE_SYSTEM_PROMPT = """Answer the user's question directly in plain sentences (no JSON, no markdown
headings). Use ONLY the evidence given below; never invent figures. Keep it
under 150 words. Cite web claims with their [n] numbers."""


async def _narrate_prose_rescue(
    user_query: str,
    rows: Sequence[dict],
    news_context: list,
    market_data: list,
) -> Optional[PipelineOutput]:
    """Last resort when structured narration fails: one plain-text call over
    the real evidence. Returns None when there is no evidence to speak from
    (then the honest generic fallback stands) or when the rescue also fails."""
    rows = list(rows or [])
    news_context = list(news_context or [])
    market_data = list(market_data or [])
    if not rows and not news_context and not market_data:
        return None
    evidence_parts = []
    if rows:
        evidence_parts.append(
            "Rows:\n"
            + "\n".join(json.dumps(row, default=str) for row in rows[:8])
        )
    if news_context:
        evidence_parts.append(
            "Web snippets:\n"
            + "\n".join(
                f"[{index}] {snippet}"
                for index, snippet in enumerate(news_context[:8], start=1)
            )
        )
    if market_data:
        evidence_parts.append(
            "Market series:\n"
            + "\n".join(
                f"{item.get('entity', 'series')}: "
                f"{list(item.get('values') or [])[-8:]}"
                for item in market_data[:4]
            )
        )
    try:
        result = await generate_response(
            prompt=(
                f"Question:\n{user_query}\n\nEvidence:\n" + "\n\n".join(evidence_parts)
            ),
            system_prompt=PROSE_RESCUE_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=600,
        )
        text = (result.get("content") or "").strip()
        if not text:
            return None
        if text.startswith("{") or text.startswith("["):
            # Not prose (a JSON blob or fragment) - refusing to present it
            # as an answer keeps the honest generic fallback.
            logger.warning("Prose rescue returned JSON, not prose; discarding.")
            return None
        return fallback_output(reason=text, confidence=0.35)
    except Exception as exc:
        logger.warning(f"Prose rescue failed: {exc}")
        return None


def evidence_confidence_cap(
    model_confidence: float,
    *,
    row_count: int,
    snippet_count: int,
    provider_count: int,
    has_market: bool,
) -> float:
    """Cap the model's confidence by what the evidence actually supports.

    A single-source comparative claim must never read "High": strong evidence
    (3+ rows/snippets, 2+ independent providers, market series) caps at 0.90,
    thin evidence at 0.65, and nothing at all at 0.35. The model's own number
    survives below the cap, so question-difficulty signal is kept.
    """
    if (
        row_count >= 3
        or snippet_count >= 3
        or provider_count >= 2
        or has_market
    ):
        cap = 0.90
    elif row_count >= 1 or snippet_count >= 1:
        cap = 0.65
    else:
        cap = 0.35
    try:
        return round(min(float(model_confidence), cap), 2)
    except (TypeError, ValueError):
        return cap


def _evidence_inventory(
    rows: Sequence[dict],
    computed_numbers: dict,
    news_context: list,
    market_data: list,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
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
        "market_is_short_term_only": bool(market_data),
        "price_history_entities": [
            str(item.get("entity", item.get("symbol", "series")))
            for item in (price_history or [])
        ],
        "financial_history_entities": [
            str(item.get("entity", item.get("symbol", "series")))
            for item in (financial_history or [])
        ],
        "fundamentals_entities": [
            str(item.get("entity", item.get("symbol", "series")))
            for item in (fundamentals or [])
        ],
        "fundamentals_is_snapshot_only": bool(fundamentals),
        "macro_entities": [
            str(item.get("entity", "series")) for item in (macro_data or [])
        ],
    }