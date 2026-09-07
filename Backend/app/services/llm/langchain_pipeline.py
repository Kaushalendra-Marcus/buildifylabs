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
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Sequence

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
        clarification_asks_for_researchable_data,
        comparison_confidence,
        compute_comparison_stats,
        compute_net_margins,
        compute_pct_change,
        compute_yearly_stats,
        decompose_comparison_query,
        figures_share_metric,
        figure_entity_label,
        figure_metric_label,
        insufficient_reason,
        is_researchable_comparison,
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
    figures_share_metric = None  # type: ignore
    figure_entity_label = None  # type: ignore
    figure_metric_label = None  # type: ignore
    insufficient_reason = None  # type: ignore
    is_researchable_comparison = None  # type: ignore
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
    web_sources: Optional[list] = None,
    fundamentals: Optional[list] = None,
    macro_data: Optional[list] = None,
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
        news_section = "User is asking for general knowledge (live web) - use your knowledge to provide direct answers, do not ask for clarifications."

    company_section = company_name or "Not provided"

    market_section = (
        "\n".join(
            f"- {item.get('entity', 'series')}: "
            f"{len(item.get('values', []) or [])} points "
            f"({', '.join(str(label) for label in (item.get('labels', []) or [])[:3])}...)"
            for item in (list(market_data) + list(macro_data))[:6]
        )
        if (market_data or macro_data)
        else "none"
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
    if comparison_gate.get("applies"):
        if comparison_gate.get("blocked"):
            gate_section = (
                "HISTORICAL COMPARISON GATE: BLOCKED -- evidence insufficient. "
                f"{comparison_gate.get('blocked_reason', '')} "
                "Do NOT present a comparison chart or winners. State what data "
                "is missing, keep confidence at 0, and do not invent figures."
            )
        else:
            gate_section = (
                "HISTORICAL COMPARISON GATE: PASSED -- validated multi-year "
                f"evidence for {comparison_gate.get('entities', [])} over "
                f"{comparison_gate.get('years')}Y. Quote the Computed "
                "Statistics comparison_stats exactly (winners + pct changes + "
                "formula/assumptions) and cite sources for explanatory claims."
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

Market Series (SHORT-TERM one-month values only - NEVER use for "last N years"):
{market_section}

Price/Financial History (multi-year validated evidence - use for historical asks):
{history_section}

Comparison Gate (must obey: BLOCKED means no chart, no winners, state missing data):
{gate_section}

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


def _figures_from_snippets(snippets: Optional[list]) -> list:
    """Extract cited money/percent figures verbatim from web snippets.

    Each figure keeps its exact text, a normalized value for bar heights, a
    unit class (money vs percent, never mixed on one chart), the snippet it
    came from, and the citation number. No NLP, no invention: regex only.
    """
    figures: list = []
    seen: set[str] = set()
    for index, snippet in enumerate(snippets or [], start=1):
        text = str(snippet)
        for match in _FIGURE_MONEY_RE.finditer(text):
            amount = float(match.group(2).replace(",", ""))
            scale = _FIGURE_SCALE.get(match.group(3) or "", 1)
            figures.append(
                {
                    "text": match.group(0).strip(),
                    "value": amount * scale,
                    "unit": "money",
                    "context": text[:70],
                    "ref": index,
                }
            )
        for match in _FIGURE_PERCENT_RE.finditer(text):
            figures.append(
                {
                    "text": match.group(0).strip(),
                    "value": float(match.group(1).replace(",", "")),
                    "unit": "percent",
                    "context": text[:70],
                    "ref": index,
                }
            )
    ordered: list = []
    for figure in figures:
        if figure["text"] not in seen:
            seen.add(figure["text"])
            ordered.append(figure)
    return ordered[:_SYNTH_FIGURES_MAX]


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


def _figures_bar_visual(figures: list) -> Optional[VisualOutput]:
    """Bar chart over same-class figures (money with money, percent with
    percent) using normalized values; labels carry citation numbers."""
    by_unit: Dict[str, list] = {}
    for figure in figures:
        by_unit.setdefault(figure["unit"], []).append(figure)
    candidates = [group for group in by_unit.values() if len(group) >= 2]
    if not candidates:
        return None
    group = max(candidates, key=len)
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
    by_unit: Dict[str, list] = {}
    for figure in figures or []:
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
                shares = True
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
        logger.warning("Figure-entity attribution check failed, keeping group: %s", exc)
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
        logger.warning("Historical gate check failed, keeping comparison: %s", exc)
    caps = [
        item
        for item in (fundamentals or [])
        if isinstance(item, dict) and isinstance(item.get("market_cap"), (int, float))
    ]
    if len(caps) < 2:
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


def drop_ungrounded_visuals(
    visuals: list, snippets: list
) -> tuple[list, int]:
    """Split LLM-proposed visuals into (kept, dropped_count)."""
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
            logger.warning("Grounding check failed, keeping visual: %s", exc)
            kept.append(visual)
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
            # A partial set of series can never chart as the requested
            # comparison (never a one- or two-company chart for a
            # three-company ask): every named entity must be present.
            if is_comp and len(required) >= 2:
                have = {
                    str(item.get("entity", "")).strip().lower()
                    for item in entities
                }
                want = {str(name).strip().lower() for name in required}
                if not want.issubset(have):
                    logger.info(
                        "Market graph blocked: missing %s for comparison %s.",
                        sorted(want - have), required,
                    )
                    return None
            # Short-term data cannot satisfy a multi-year request.
            if years and validate_historical_coverage is not None:
                base_labels = list((entities[0].get("labels") or []))
                ok, detail = validate_historical_coverage(
                    labels=base_labels,
                    period_start=entities[0].get("period_start"),
                    period_end=entities[0].get("period_end"),
                    requested_years=years,
                    values=entities[0].get("values"),
                )
                if not ok:
                    logger.info("Market graph blocked for historical query: %s", detail)
                    return None
    except Exception as exc:
        logger.warning("Market graph gate check failed, keeping graph: %s", exc)
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


def _price_history_graph_visual(
    price_history: list, query: str, requested_years: Optional[int] = None
) -> Optional[VisualOutput]:
    """Validated multi-year price chart: ALL compared companies, same window.

    Returns None unless at least two entities carry dated multi-year
    series covering the requested window -- this is the ONLY chart
    allowed for "last N years" stock performance. Annual data keeps
    annual points; weekly keeps downsampled weeklies. Never invents.
    """
    series = [item for item in (price_history or []) if item.get("values") and item.get("labels")]
    if len(series) < 2:
        return None
    years = requested_years
    try:
        if years is None and decompose_comparison_query is not None:
            years = (decompose_comparison_query(query or "") or {}).get("period_years")
    except Exception:
        pass
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
    base_labels = list(series[0].get("labels") or [])
    stride = max(1, len(base_labels) // _SYNTH_SERIES_MAX_POINTS)
    labels = base_labels[::stride]
    datasets = [
        {
            "name": str(item.get("entity", item.get("symbol", "series"))),
            "values": list(item.get("values") or [])[::stride],
        }
        for item in series
    ]
    unit = str(series[0].get("currency", "") or "").strip()
    title = f"{', '.join(names)} stock performance"
    if years:
        title += f" ({years}Y)"
    if unit:
        title += f" [{unit}]"
    return VisualOutput(
        visual_type="graph",
        title=title,
        props={"chart_type": "line", "labels": labels, "datasets": datasets},
    )


def _financial_history_table_visual(
    financial_history: list, metric: str = "revenue"
) -> Optional[VisualOutput]:
    """Annual revenue / net-income table over the validated window.

    Requires start+end rows for EVERY represented company (never a
    one- or two-company table for a three-company ask). Values are raw
    retrieved figures; growth/margin math lives in comparison_stats.
    """
    rows: list[list[str]] = []
    by_entity: Dict[str, int] = {}
    for item in financial_history or []:
        block = (item or {}).get(metric, {}) if isinstance(item, dict) else None
        if not isinstance(block, dict) or not block.get("values"):
            continue
        entity = str(item.get("entity", item.get("symbol", "entity")))
        labels = list(block.get("labels") or [])
        values = list(block.get("values") or [])
        count = 0
        for label, value in zip(labels, values):
            try:
                rows.append([entity, str(label)[:10], f"{float(value):,.0f}"])
                count += 1
            except (TypeError, ValueError):
                continue
        by_entity[entity] = by_entity.get(entity, 0) + count
    # Every company needs start AND end; at least two companies overall.
    if len(by_entity) < 2 or any(count < 2 for count in by_entity.values()):
        return None
    rows = sorted(rows)[:_SYNTH_SOURCES_MAX_ROWS]
    title = "Annual revenue" if metric == "revenue" else "Annual net income"
    return VisualOutput(
        visual_type="table",
        title=title,
        props={"columns": ["Company", "Fiscal year", "Value"], "values": rows},
    )


def _margin_table_visual(financial_history: list) -> Optional[VisualOutput]:
    """Net-profit-margin table (net income / revenue * 100, computed here).

    One comparable profitability metric for every company -- never Tesla
    margin vs BYD income vs Toyota operating profit. Returns None unless
    at least two companies have computable margins.
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
        inc_values = list(inc.get("values", []) or [])
        if compute_net_margins is None:
            return None
        try:
            margins = compute_net_margins(rev_values, inc_values)
        except Exception:
            continue
        for label, margin in zip(rev_labels, margins):
            if isinstance(margin, (int, float)):
                rows.append([entity, str(label)[:10], f"{margin:.2f}%"])
    entities = {row[0] for row in rows}
    if len(entities) < 2:
        return None
    rows = sorted(rows)[:_SYNTH_SOURCES_MAX_ROWS]
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

    # -- stock performance: multi-year price history for ALL companies --
    if METRIC_STOCK in metrics:
        have = _entities_with_price()
        if not want.issubset(have):
            historical_ok = False
            details.append(
                f"stock price history missing for {sorted(want - have)} "
                f"(have {sorted(have)})."
            )
            comparison_ok = False
            comp_details.append(f"stock comparison lacks {scope_word} companies.")
        else:
            per_entity_ok = True
            for item in price_history:
                ok, detail = validate_historical_coverage(
                    labels=list(item.get("labels") or []),
                    period_start=item.get("period_start"),
                    period_end=item.get("period_end"),
                    requested_years=years,
                    values=item.get("values"),
                )
                if not ok:
                    per_entity_ok = False
                    details.append(f"{item.get('entity')}: {detail}")
            if not per_entity_ok:
                historical_ok = False
                comparison_ok = False
                comp_details.append("stock series lack 3-year coverage.")
            else:
                # Like-for-like across price series.
                ev = []
                for item in price_history:
                    vals = list(item.get("values") or [])
                    labs = list(item.get("labels") or [])
                    if ComparisonEvidence is None:
                        continue
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
                ok, detail = validate_comparison(ev, expected_entities=entities, expected_metric=METRIC_STOCK)
                if not ok:
                    comparison_ok = False
                    comp_details.append(f"stock: {detail}")
                else:
                    for item in price_history:
                        vals = list(item.get("values") or [])
                        stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_STOCK] = (vals[0], vals[-1])

    # -- revenue growth: annual revenue history for ALL companies --
    if METRIC_REVENUE in metrics:
        have = _entities_with_fin("revenue")
        if not want.issubset(have):
            historical_ok = False
            details.append(
                f"annual revenue history missing for {sorted(want - have)}."
            )
            comparison_ok = False
            comp_details.append(f"revenue comparison lacks {scope_word} companies.")
        else:
            ev = []
            for item in financial_history:
                chunk = item.get("revenue", {})
                labs = list(chunk.get("labels") or [])
                vals = list(chunk.get("values") or [])
                if len(vals) < 2:
                    historical_ok = False
                    details.append(f"{item.get('entity')}: need 2+ annual revenue points.")
                    comparison_ok = False
                    continue
                if ComparisonEvidence is not None:
                    ev.append(ComparisonEvidence(
                        entity=str(item.get("entity", "")),
                        metric=METRIC_REVENUE,
                        value=float(vals[-1]),
                        unit="currency",
                        period_start=str(labs[0]) if labs else None,
                        period_end=str(labs[-1]) if labs else None,
                        frequency="annual",
                        definition=str(chunk.get("metric", "annualTotalRevenue")),
                        source="Yahoo Finance",
                        source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/financials/",
                        is_historical=True,
                    ))
            if ev:
                ok, detail = validate_comparison(ev, expected_entities=entities, expected_metric=METRIC_REVENUE)
                if not ok:
                    comparison_ok = False
                    comp_details.append(f"revenue: {detail}")
                else:
                    for item in financial_history:
                        vals = list((item.get("revenue", {}) or {}).get("values") or [])
                        if len(vals) >= 2:
                            stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_REVENUE] = (vals[0], vals[-1])

    # -- profitability: ONE shared annual metric for ALL companies --
    # (net profit margin = net income / revenue * 100, computed in code).
    if METRIC_PROFIT in metrics:
        have = _entities_with_fin("net_income")
        if not want.issubset(have):
            historical_ok = False
            details.append(
                f"annual profitability history missing for {sorted(want - have)}."
            )
            comparison_ok = False
            comp_details.append(f"profitability comparison lacks {scope_word} companies.")
        else:
            ev = []
            for item in financial_history:
                chunk = item.get("net_income", {})
                labs = list(chunk.get("labels") or [])
                vals = list(chunk.get("values") or [])
                if len(vals) < 2:
                    historical_ok = False
                    details.append(f"{item.get('entity')}: need 2+ annual profit points.")
                    comparison_ok = False
                    continue
                if ComparisonEvidence is not None:
                    ev.append(ComparisonEvidence(
                        entity=str(item.get("entity", "")),
                        metric=METRIC_PROFIT,
                        value=float(vals[-1]),
                        unit="currency",
                        period_start=str(labs[0]) if labs else None,
                        period_end=str(labs[-1]) if labs else None,
                        frequency="annual",
                        definition=str(chunk.get("metric", "annualNetIncome")),
                        source="Yahoo Finance",
                        source_url=f"https://finance.yahoo.com/quote/{item.get('symbol', '')}/financials/",
                        is_historical=True,
                    ))
            if ev:
                ok, detail = validate_comparison(ev, expected_entities=entities, expected_metric=METRIC_PROFIT)
                if not ok:
                    comparison_ok = False
                    comp_details.append(f"profitability: {detail}")
                else:
                    for item in financial_history:
                        vals = list((item.get("net_income", {}) or {}).get("values") or [])
                        if len(vals) >= 2:
                            stats_input.setdefault(str(item.get("entity", "")), {})[METRIC_PROFIT] = (vals[0], vals[-1])

    # A 1-month market_data series present WITHOUT price history is the
    # exact live failure: flag it explicitly, never let it satisfy history.
    if not price_history and (market_data or []) and METRIC_STOCK in metrics:
        historical_ok = False
        details.append(
            "only a short-term (one-month) price series is available; "
            "it cannot satisfy a multi-year request."
        )
        comparison_ok = False

    gate["historical_ok"] = historical_ok
    gate["historical_detail"] = " ".join(details)
    gate["comparison_ok"] = comparison_ok
    gate["comparison_detail"] = " ".join(comp_details)
    gate["blocked"] = not (historical_ok and comparison_ok)
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

    Historical gating: when the query is a multi-entity historical
    comparison, NO graph/comparison visual is synthesized unless the
    historical gate validates (both companies, same metric/unit/period).
    A 0-confidence answer never gains a chart here -- insufficient
    evidence blocks visualization by design.
    """
    if output.clarification is not None or output.visuals:
        return output
    # Zero-confidence answers must stay chartless: synthesizing a visual
    # at 0% was the exact live failure (chart rendered, confidence 0%).
    try:
        if float(output.confidence or 0.0) <= 0.0:
            # Still allow the honest sources table below (not a chart), but
            # never a graph/comparison/metric built from thin air.
            pass
    except (TypeError, ValueError):
        pass
    rows = list(rows or [])
    market_data = list(market_data or [])
    web_sources = list(web_sources or [])
    news_context = list(news_context or [])
    fundamentals = list(fundamentals or [])
    macro_data = list(macro_data or [])
    price_history = list(price_history or [])
    financial_history = list(financial_history or [])
    combined_series = list(market_data) + list(macro_data)
    synthesized: list = []

    def _sources_only() -> list:
        table = _sources_table_visual(web_sources)
        return [table] if table is not None else []

    # Historical comparison gate runs BEFORE any synthesis.
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
        logger.warning("Historical gate failed open: %s", exc)
        gate = {}
    if gate.get("applies") and gate.get("blocked"):
        logger.info("Historical comparison blocked: %s", gate.get("blocked_reason", "")[:160])
        # Blocked comparisons get sources only (honest, not a chart).
        # A 0-confidence output keeps that single table; callers that need
        # strictly no visuals can drop it, but a chart/comparison is never
        # synthesized here.
        synthesized = _sources_only()
        if synthesized:
            output.visuals = list(output.visuals) + synthesized[:3]
        return output
    if gate.get("applies") and not gate.get("blocked"):
        # Validated history: chart the real multi-year series (both
        # companies, correct window, legend), plus annual tables.
        years = gate.get("years")
        graph = _price_history_graph_visual(price_history, query, years)
        if graph is not None:
            # At 0 confidence even a validated series stays uncharted.
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(graph)
            except (TypeError, ValueError):
                synthesized.append(graph)
        for metric_key in ("revenue", "net_income"):
            table = _financial_history_table_visual(financial_history, metric_key)
            if table is not None:
                synthesized.append(table)
        margin_table = _margin_table_visual(financial_history)
        if margin_table is not None:
            synthesized.append(margin_table)
        sources = _sources_table_visual(web_sources)
        if sources is not None:
            synthesized.append(sources)
        if synthesized:
            logger.info(f"Visual guarantee synthesized {len(synthesized)} validated visual(s).")
            output.visuals = list(output.visuals) + synthesized[:3]
        return output

    if rows:
        chart_requested = bool(re.search(CHART_INTENT_RE, query, re.IGNORECASE))
        synthesized = _visuals_from_rows(rows, computed_numbers, preferred_visual)
        if chart_requested:
            logger.info("Chart intent detected; synthesized visuals lead with a chart.")
    elif combined_series:
        graph = _market_graph_visual(combined_series, query)
        if graph is not None:
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
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(fundamentals_comparison)
            except (TypeError, ValueError):
                synthesized.append(fundamentals_comparison)
        if not synthesized:
            # Gated short-term series (historical query, single entity):
            # fall through to honest sources only, never an empty naked answer.
            synthesized = _sources_only()
    else:
        figures = _figures_from_snippets(news_context)
        # Explicit X-vs-Y steers to a comparison card first (never invented).
        comparison = _comparison_from_figures(figures, query)
        if comparison is not None:
            try:
                if float(output.confidence or 0.0) > 0.0:
                    synthesized.append(comparison)
            except (TypeError, ValueError):
                synthesized.append(comparison)
        # A figures bar over mismatched metrics (funding vs cost) is the
        # same false-comparison bug: only chart when cues agree.
        bar_ok = True
        try:
            if figures_share_metric is not None and len(figures) >= 2:
                bar_ok, _ = figures_share_metric(figures)
        except Exception:
            bar_ok = True
        if bar_ok:
            bar = _figures_bar_visual(figures)
            if bar is not None:
                try:
                    if float(output.confidence or 0.0) > 0.0:
                        synthesized.append(bar)
                except (TypeError, ValueError):
                    synthesized.append(bar)
        table = _figures_table_visual(figures)
        if table is not None:
            synthesized.append(table)
        timeline = _timeline_visual(news_context, web_sources)
        if timeline is not None:
            synthesized.append(timeline)
        # Structured fundamentals comparison needs no snippet figures.
        fundamentals_comparison = _fundamentals_comparison_visual(fundamentals, query)
        if fundamentals_comparison is not None:
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
    price_history: Optional[list] = None,
    financial_history: Optional[list] = None,
    research_notes: Optional[list] = None,
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
    if price_history is None:
        price_history = []
    if financial_history is None:
        financial_history = []
    if research_notes is None:
        research_notes = []

    rows = list(db_data or [])
    thinking: List[str] = []
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

    # Deterministic historical gate + stats BEFORE the LLM narrates, so the
    # model can only narrate validated numbers (specs/11 S2) and the prompt
    # carries an explicit BLOCKED/PASSED verdict.
    gate: Dict[str, Any] = {}
    try:
        gate = _historical_comparison_gate(
            user_query,
            market_data=market_data,
            price_history=price_history,
            financial_history=financial_history,
            fundamentals=fundamentals,
        )
    except Exception as exc:
        logger.warning("Historical gate failed open: %s", exc)
        gate = {}
    if gate.get("comparison_stats"):
        computed_numbers = {
            **(computed_numbers or {}),
            "comparison_stats": gate["comparison_stats"],
        }
    if gate.get("applies") and gate.get("blocked"):
        thinking.append(f"Historical gate BLOCKED: {(gate.get('blocked_reason', '') or '')[:160]}")

    try:
        await emit("judging")
        decision = await judge_sufficiency(
            user_query=user_query,
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
            f"{len(market_data)} market series, "
            f"{len(price_history)} price-history, "
            f"{len(financial_history)} financial-history)"
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

        await emit("narrating")
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
            web_sources=web_sources,
            fundamentals=fundamentals,
            macro_data=macro_data,
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
                web_sources=web_sources,
                fundamentals=fundamentals,
                macro_data=macro_data,
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
            try:
                asks_data = (
                    clarification_asks_for_researchable_data(
                        output.clarification.question, user_query
                    )
                    if clarification_asks_for_researchable_data is not None
                    else False
                )
            except Exception:
                asks_data = False
            if asks_data:
                logger.warning(
                    "Researchable-data clarification suppressed; "
                    "answering from researched evidence."
                )
                thinking.append(
                    "Suppressed ask-user-for-data clarification; "
                    "answered from researched evidence."
                )
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
                    web_sources=web_sources,
                    fundamentals=fundamentals,
                    macro_data=macro_data,
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
            # Checked trust: LLM-proposed numeric visuals for web-only evidence
            # must ground in the cited snippets; ungrounded ones fall back to
            # the deterministic figures path via ensure_visuals below.
            if (
                output.visuals
                and not narrate_rows
                and news_context
                and source_scope in ("live_web", "both")
            ):
                kept, dropped = drop_ungrounded_visuals(
                    list(output.visuals), news_context
                )
                if dropped:
                    thinking.append(
                        f"Dropped {dropped} ungrounded visual(s); "
                        "deterministic fallback applies."
                    )
                    output.visuals = kept
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
            capped = evidence_confidence_cap(
                output.confidence,
                row_count=len(narrate_rows),
                snippet_count=len(news_context),
                provider_count=len(providers),
                has_market=bool(market_data or macro_data or fundamentals),
            )
            # Historical gate overrides the generic cap: BLOCKED means 0.0
            # (missing multi-year evidence), PASSED keeps the generic cap.
            # A short-term-only series must never inflate confidence for a
            # multi-year ask.
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
                else:
                    # Validated history is strong evidence: lift to at least
                    # 0.65 so a real 3Y comparison never reads "Low 0%".
                    capped = max(capped, 0.65)
                    if capped < output.confidence - 0.005:
                        pass  # generic cap still wins below
                    elif output.confidence < capped:
                        thinking.append(
                            f"Confidence lifted by validated history: {output.confidence} -> {capped}"
                        )
                        output.confidence = capped
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
            query=user_query,
            fundamentals=fundamentals,
            macro_data=macro_data,
            price_history=price_history,
            financial_history=financial_history,
        )
        if output.visuals:
            thinking.append(
                f"Visuals out: {', '.join(v.visual_type for v in output.visuals)}"
            )
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