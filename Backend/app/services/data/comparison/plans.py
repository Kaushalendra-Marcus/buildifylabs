"""Research plans, tool routing, clarification policy. Split from comparison.py; behavior unchanged."""

from __future__ import annotations

import logging
import re

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .symbols import METRIC_PROFIT, METRIC_REVENUE, METRIC_STOCK, financial_entities, market_candidate_entities, symbol_for_entity
from .timeframe import TimeRange, detect_generic_metric_phrases, detect_metrics, is_comparison_query, parse_time_range
from .entities import detect_entities

logger = logging.getLogger(__name__)



def _legacy_build_research_plan_removed() -> None:
    """Legacy dict-only build_research_plan was removed (Phase H1).

    The canonical builder is build_research_plan() defined below (ResearchPlan
    dict subclass with required_tools). This stub exists only to document the
    removal: there is exactly ONE definition of "required tools" --
    required_tools_for_query() -- read via ResearchPlan.required_tools.
    """
    raise NotImplementedError("use build_research_plan()")


def is_researchable_comparison(query: str) -> bool:
    """True when the query is a multi-entity comparison whose entities and
    metrics are detected -- i.e. public data the agent must research itself
    rather than ask the user to supply.

    Generic: works for arbitrary entities/metrics, not a fixed list. The
    period is safely defaultable (latest available window), so it is NOT
    required here; historical intent only strengthens the verdict via the
    plan's requires_history flag when present.
    """
    try:
        plan = build_research_plan(query or "")
    except Exception:
        return False
    entities = list(plan.get("entities", []) or [])
    metrics = list(plan.get("metrics", []) or [])
    if not (plan.get("is_comparison") and len(entities) >= 2):
        return False
    if len(metrics) >= 1:
        return True
    # Arbitrary metrics: a generic metric phrase also counts as supplied
    # (never ask the user for a metric they already named).
    try:
        if detect_generic_metric_phrases(query or ""):
            return True
    except Exception:
        pass
    return False


# A clarification that asks the USER to supply researchable company data
# ("Please provide annual revenue / net income / profit margin figures ...").
# Such questions must never be asked for public-company statistics: the
# agent has web/data tools and must use them instead.
_RESEARCHABLE_DATA_REQUEST_RE = re.compile(
    r"\b(provide|supply|share|upload|enter|give|paste|tell\s+me)\b"
    r".{0,80}?\b(annual\s+)?(revenue|net\s*income|profit\s*margin|"
    r"profitability|financials?|figures?)\b"
    r"|\b(annual\s+)?(revenue|net\s*income|profit\s*margin)\s+"
    r"(figures?|numbers?|data)\s+for\b",
    re.IGNORECASE,
)


def clarification_asks_for_researchable_data(
    question: str, query: str
) -> bool:
    """True when a clarification question asks the user to hand over data
    the agent should research itself (and the original query is a
    researchable public-company comparison)."""
    if not question or not is_researchable_comparison(query or ""):
        return False
    return bool(_RESEARCHABLE_DATA_REQUEST_RE.search(question or ""))


def decompose_comparison_query(query: str) -> Dict[str, Any]:
    """Decompose a comparison query into entities / metrics / period.

    Pure and deterministic -- no LLM, no network.
    """
    entities = detect_entities(query or "")
    metrics = detect_metrics(query or "")
    time_range = parse_time_range(query or "")
    years = time_range.years
    return {
        "entities": entities,
        "metrics": metrics,
        "period_years": years,
        "period_label": time_range.label or (f"{years}Y" if years else None),
        "time_range": time_range.to_dict(),
        "is_comparison": is_comparison_query(query or "")
        or (len(entities) >= 2 and len(metrics) >= 1),
        "requires_history": years is not None and years >= 2,
    }


# ---------------------------------------------------------------------------
# Canonical research plan: the ONE source of truth for research execution.
# ---------------------------------------------------------------------------
_MACRO_INTENT_RE = re.compile(
    r"\b(inflation|cpi|consumer\s*price|interest\s*rate|fed\s*funds?|"
    r"federal\s*funds|unemployment|jobless|jobs\s*report|gdp|"
    r"recession|treasury\s*yield|mortgage\s*rate)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_WHO_WHAT_RE = re.compile(
    r"\b(who\s+is|who\s+are|what\s+is|what\s+are|tell\s+me\s+about|"
    r"explain|overview\s+of)\b",
    re.IGNORECASE,
)

# Deterministic tool requirements per evidence kind. The LLM planner may
# RECOMMEND tools but can never remove these: resolve_tool_plan() unions
# them back in. Tool names match the TOOL_CATALOG keys in langchain_pipeline.
HISTORY_TOOLS = ("market_history", "financial_history")
SNAPSHOT_TOOLS = ("market", "fundamentals")
_TOOL_CATALOG_KEYS = frozenset({
    "snippets", "market", "fundamentals", "market_history",
    "financial_history", "wikipedia", "macro", "extract",
})


def required_tools_for_query(
    query: str, decomposed: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Deterministic tool requirements for a query (no LLM, no network).

    This is the MINIMAL correctness set -- tools without which the answer
    would be wrong or missing -- not a maximal wish list. The LLM planner
    stays advisory for everything else (snippets on simple asks, wikipedia,
    macro, extract): it may ADD tools via resolve_tool_plan(), but it can
    never remove these:

    - Historical stock intent -> market_history (never the 1-month snapshot).
    - Historical revenue/profitability intent -> financial_history.
    - Non-historical stock intent -> market; scale/valuation intent ->
      fundamentals.
    - Named multi-entity historical comparisons ALWAYS need market_history +
      financial_history + fundamentals (current scale context) + snippets
      (the explanatory "why"), for EVERY named company.
    """
    text = query or ""
    decomposed = decomposed or decompose_comparison_query(text)
    entities = list(decomposed.get("entities", []) or [])
    metrics = list(decomposed.get("metrics", []) or [])
    requires_history = bool(decomposed.get("requires_history"))
    is_comp = bool(decomposed.get("is_comparison"))
    required: List[str] = []

    def _add(tool: str) -> None:
        if tool not in required:
            required.append(tool)

    # Entity typing gate: financial entities plus UNKNOWN names (whose
    # Yahoo symbol search is the ground-truth typing step) may require
    # market adapters. Known non-financial entities (geographies, concepts,
    # categories, products, private companies) resolve via
    # snippets/wikipedia/macro only.
    try:
        fin_entities = market_candidate_entities(entities)
    except Exception:
        fin_entities = financial_entities(entities)
    has_financial = bool(fin_entities)
    needs_stock = METRIC_STOCK in metrics
    needs_financial = METRIC_REVENUE in metrics or METRIC_PROFIT in metrics
    if is_comp and len(entities) >= 2 and requires_history:
        # Canonical multi-entity historical comparison requirement.
        # History tools only when at least one financial entity exists;
        # otherwise snippets carry the comparison (never Yahoo for concepts).
        if has_financial and (needs_stock or not metrics):
            _add("market_history")
        if has_financial and (needs_financial or not metrics):
            _add("financial_history")
        if has_financial:
            _add("fundamentals")
        _add("snippets")
    else:
        if needs_stock and has_financial:
            _add("market_history" if requires_history else "market")
        elif needs_stock and not has_financial:
            _add("snippets")
        if needs_financial and has_financial:
            _add("financial_history" if requires_history else "fundamentals")
        elif needs_financial and not has_financial:
            _add("snippets")
        if entities and not (needs_stock or needs_financial) and not is_comp:
            if has_financial:
                _add("fundamentals")
            else:
                _add("snippets")
        if is_comp and len(entities) >= 2:
            _add("snippets")
    return required


def resolve_tool_plan(llm_plan: Any, query: str) -> List[str]:
    """The ONE authoritative tool plan for research execution.

    Union of the deterministic requirements (which always win) plus any
    valid extra tools the LLM recommended. The LLM may ADD tools; it must
    NEVER silently remove a deterministically required one (e.g. proposing
    only ["market_history"] for a query that also needs financial_history).
    An empty/invalid LLM plan means "no opinion" and resolves to [] so the
    caller falls back to the deterministic intent predicates -- unknown
    names are dropped, never executed. Pure -- no LLM, no network.
    """
    if not isinstance(llm_plan, list) or not llm_plan:
        return []
    required = required_tools_for_query(query or "")
    extras: List[str] = []
    for item in llm_plan:
        name = str(item or "").strip().lower()
        if name in _TOOL_CATALOG_KEYS and name not in required and name not in extras:
            extras.append(name)
    return required + extras


class ResearchPlan(dict):
    """Canonical internal research plan: the single source of truth that
    research execution, gating, confidence, and follow-ups all read from.

    A dict subclass (not a second competing model): all legacy dict access
    (plan["entities"], plan.get("tasks"), JSON serialization) keeps working,
    while typed attribute access (plan.entities, plan.required_tools, ...)
    is available for new code. Built only by build_research_plan(), which
    reuses decompose_comparison_query() -- never construct a competing
    planner, extend this one.
    """

    query: str = ""
    entities: List[str] = []
    metrics: List[str] = []
    source_scope: str = "live_web"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Typed attribute views over the same canonical mapping.
        self.query = str(self.get("query", "") or "")
        self.entities = list(self.get("entities", []) or [])
        self.metrics = list(self.get("metrics", []) or [])
        self.source_scope = str(self.get("source_scope", "live_web") or "live_web")

    @property
    def time_range(self) -> TimeRange:
        return TimeRange.from_dict(self.get("time_range"))

    @property
    def comparison_requested(self) -> bool:
        return bool(self.get("comparison_requested", self.get("is_comparison", False)))

    @property
    def requires_history(self) -> bool:
        return bool(self.get("requires_history", False))

    @property
    def required_tools(self) -> List[str]:
        return list(self.get("required_tools", []) or [])

    @property
    def research_tasks(self) -> List[Dict[str, Any]]:
        tasks = self.get("research_tasks", self.get("tasks", []))
        return list(tasks or [])

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


def build_research_plan(query: str, source_scope: str = "live_web") -> ResearchPlan:
    """Canonical plan builder (single source of truth for research execution).

    Returns a ResearchPlan (a dict subclass): legacy dict access keeps
    working and typed attribute access is available for new code.
    """
    return _build_research_plan(query or "", source_scope=source_scope)


def _build_research_plan(query: str, source_scope: str = "live_web") -> ResearchPlan:
    """Deterministic internal research plan for a comparison query.

    Decomposes the request into entities / metrics / period plus one task
    per entity per evidence kind, so the research stage can verify
    entity-completeness (never silently continue with 2 of 3 companies)
    and metric-completeness (stock history alone never satisfies revenue
    or profitability). Pure -- no LLM, no network.
    """
    decomposed = decompose_comparison_query(query or "")
    entities = list(decomposed.get("entities", []) or [])
    metrics = list(decomposed.get("metrics", []) or [])
    time_range = parse_time_range(query or "")
    years = time_range.years
    tasks: List[Dict[str, Any]] = []
    for entity in entities:
        symbol = symbol_for_entity(entity)
        if METRIC_STOCK in metrics:
            tasks.append({
                "id": f"stock:{entity}", "kind": "stock_history",
                "entity": entity, "symbol": symbol, "years": years,
            })
        if METRIC_REVENUE in metrics:
            tasks.append({
                "id": f"revenue:{entity}", "kind": "financial_history",
                "entity": entity, "symbol": symbol, "years": years,
                "metric": "annualTotalRevenue",
            })
        if METRIC_PROFIT in metrics:
            tasks.append({
                "id": f"profit:{entity}", "kind": "financial_history",
                "entity": entity, "symbol": symbol, "years": years,
                "metric": "annualNetIncome",
            })
    if metrics:
        tasks.append({
            "id": "explain", "kind": "web_explanation",
            "entities": entities, "metrics": metrics,
        })
    tasks.append({"id": "normalize", "kind": "normalize"})
    tasks.append({"id": "validate", "kind": "validate"})
    tasks.append({"id": "calculate", "kind": "calculate"})
    tasks.append({"id": "answer", "kind": "answer"})
    return ResearchPlan({
        "query": query,
        "entities": entities,
        "metrics": metrics,
        "time_range": time_range.to_dict(),
        "period_years": years,
        "period_label": time_range.label or (f"{years}Y" if years else None),
        "is_comparison": bool(decomposed.get("is_comparison", False)),
        "comparison_requested": bool(decomposed.get("is_comparison", False)),
        "requires_history": bool(decomposed.get("requires_history", False)),
        "required_entities": entities,
        "required_metrics": metrics,
        "required_tools": required_tools_for_query(query, decomposed),
        "tasks": tasks,
        "research_tasks": tasks,
        "source_scope": source_scope,
    })


def plan_to_dict(plan: Any) -> Dict[str, Any]:
    """Coerce a ResearchPlan (or legacy dict) to the canonical dict shape."""
    if isinstance(plan, ResearchPlan):
        return plan.to_dict()
    if isinstance(plan, dict):
        return dict(plan)
    return {}


def reconcile_judge_tools(
    judge_tools: Any, evidence: Dict[str, Any]
) -> Dict[str, Any]:
    """Post-hoc role of Decision.tools_needed (it arrives AFTER research).

    The judge's tool list cannot drive dispatch (research already ran), so
    its only honest use is a completeness check: which requested tools have
    no evidence on hand. Returns {"missing": [...], "note": str}. Callers
    must cap confidence / state assumptions when "missing" is non-empty --
    never silently ignore it (that was the dead-field bug).
    """
    wanted = [
        str(item or "").strip().lower() for item in (judge_tools or [])
        if str(item or "").strip()
    ]
    evidence = evidence or {}
    has_map = {
        "snippets": bool(evidence.get("web_snippet_count")),
        "market": bool(evidence.get("market_entities")),
        "market_history": bool(evidence.get("price_history_entities")),
        "financial_history": bool(evidence.get("financial_history_entities")),
        "fundamentals": bool(evidence.get("fundamentals_entities")),
        "wikipedia": bool(evidence.get("web_snippet_count")),
        "macro": bool(evidence.get("macro_entities")),
        "extract": bool(evidence.get("web_snippet_count")),
    }
    missing = [tool for tool in wanted if not has_map.get(tool, True)]
    note = (
        f"Judge-requested tools lacking evidence: {missing}."
        if missing else ""
    )
    return {"missing": missing, "note": note}


def merge_clarification_context(
    original_query: str,
    prior_clarification: Optional[str],
    followup_query: str,
) -> str:
    """Merge a clarification reply into the ORIGINAL research plan context.

    A clarification answer ("revenue", "Tesla") is a fragment, not a
    standalone query: decomposing it alone loses the original entities and
    period. Returns the combined text to decompose (original query first so
    its entities/metrics/period win on conflicts, then the user's answer).
    Pure -- no LLM, no network.
    """
    try:
        from app.services.data.canonical import build_canonical_query as _build
        return _build(original_query or "", prior_clarification, followup_query or "")
    except Exception:
        pass
    original = (original_query or "").strip()
    followup = (followup_query or "").strip()
    if not original:
        return followup
    if not followup:
        return original
    # The follow-up refines the original; keep both so entity/metric/period
    # detection sees the full intent instead of a bare fragment.
    return f"{original} [clarification answer: {followup}]"


def must_not_clarify(query: str) -> bool:
    """Deterministic clarification ban for researchable public comparisons.

    True when the query names >=2 entities plus >=1 metric (canonical or
    generic phrase) -- the data is theoretically retrievable, so asking the
    user to supply it is forbidden. The period is safely defaultable and
    never justifies a clarification on its own. Genuine ambiguity
    ("Compare Apple and Samsung" with no metric) returns False and may
    still clarify.
    """
    return is_researchable_comparison(query or "")


# ---------------------------------------------------------------------------
# Generic clarification discipline (arbitrary entities / metrics / timeframes
# / geographies / currencies / source preferences).
# ---------------------------------------------------------------------------
_METRIC_WORD_RE = re.compile(
    r"\b(revenue|revenues|sales|profit\w*|margin|margins|earnings|stock|"
    r"share|price|market|growth|headcount|churn|units?|users?|customers?|"
    r"orders?|returns?|top[-\s]?line|bottom[-\s]?line)\b",
    re.IGNORECASE,
)
_TIMEFRAME_WORD_RE = re.compile(
    r"\b(last|past|previous|trailing|over|years?|yrs?|months?|weeks?|"
    r"quarters?|20\d{2}|FY\s?20\d{2}|fiscal)\b",
    re.IGNORECASE,
)
_GEO_WORD_RE = re.compile(
    r"\b(usa|america|europe|asia|china|india|japan|germany|france|uk|"
    r"region|regions|country|countr\w*|geograph\w*|global|local|us|eu)\b",
    re.IGNORECASE,
)
_CURRENCY_WORD_RE_CLAR = re.compile(
    r"\b(usd|eur|cny|jpy|inr|gbp|currency|currencies|dollars?|yuan|yen|"
    r"euros?|rupees?|pounds?)\b",
    re.IGNORECASE,
)
_SOURCE_WORD_RE = re.compile(
    r"\b(source|sources|provider|yahoo|tavily|fred|wikipedia|web|live)\b",
    re.IGNORECASE,
)


def clarification_is_redundant(question: str, query: str) -> Tuple[bool, str]:
    """True when a clarification asks for info already in the query.

    Generic across entities, metrics, timeframes, geographies, currencies,
    and source preferences. Timeframe / geography / currency / source are
    safely defaultable and must never trigger a clarification when
    entities+metrics are present. Pure.
    """
    q = (question or "").strip()
    orig = (query or "").strip()
    if not q or not orig:
        return False, ""
    ql, ol = q.lower(), orig.lower()
    # Entities already supplied: question names an entity span already present.
    try:
        for entity in detect_entities(orig):
            if entity and entity.lower() in ql and entity.lower() in ol:
                # Asking "which company" when companies are named is redundant.
                if re.search(r"\b(which|what)\b.*\b(compan\w*|entit\w*|firm\w*)\b", ql):
                    return True, f"entities already supplied ({entity})"
    except Exception:
        pass
    # Metrics already supplied: canonical metric present. A bare generic
    # phrase ("by users") does NOT make "which user metric?" redundant --
    # that clarification disambiguates sub-metrics (total vs active) and
    # must be allowed. Only a canonical metric (revenue/stock/profit) plus
    # a question asking for that same dimension is redundant.
    try:
        supplied = set(detect_metrics(orig))
        if supplied and (
            _METRIC_WORD_RE.search(q)
            or re.search(r"\b(metric|measure|kpi|dimension)\b", ql)
        ):
            # Question asks for a metric dimension already named.
            for token in re.findall(r"[a-z\-]+", ol):
                if token in ql and _METRIC_WORD_RE.search(token):
                    return True, f"metric already supplied ({token})"
            if re.search(r"\b(metric|measure|kpi|dimension)\b", ql):
                # Only when the query names a canonical metric does a bare
                # "which metric?" repeat it. Generic-only queries may still
                # need disambiguation.
                return True, "metric already supplied"
    except Exception:
        pass
    # Safely defaultable dimensions never justify clarification when the
    # core comparison (entities+metrics or entities+intent) is present.
    try:
        has_core = len(detect_entities(orig)) >= 1
        if has_core:
            if _TIMEFRAME_WORD_RE.search(q) and parse_time_range(orig).years is not None:
                return True, "timeframe already supplied"
            if _TIMEFRAME_WORD_RE.search(q) and (
                detect_metrics(orig) or detect_generic_metric_phrases(orig)
            ):
                return True, "timeframe safely defaultable"
            if _GEO_WORD_RE.search(q):
                return True, "geography safely defaultable"
            if _CURRENCY_WORD_RE_CLAR.search(q):
                return True, "currency safely defaultable"
            if _SOURCE_WORD_RE.search(q):
                return True, "source safely defaultable"
    except Exception:
        pass
    return False, ""


def should_clarify_generic(
    query: str,
    *,
    entities: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[str]] = None,
    evidence_sufficient: bool = False,
    prior_clarification: Optional[str] = None,
    proposed_question: Optional[str] = None,
) -> Tuple[bool, str]:
    """Generic clarification gate: only when missing blocks execution.

    Returns (allowed, reason). Clarification is forbidden when evidence is
    sufficient for (partial) execution, when the proposed question repeats
    prior clarification, or when it asks for already-supplied / defaultable
    info. Pure.
    """
    if evidence_sufficient:
        return False, "evidence sufficient for execution"
    if prior_clarification and proposed_question:
        try:
            import difflib as _difflib

            left = "".join(c for c in prior_clarification.lower() if c.isalnum())
            right = "".join(c for c in proposed_question.lower() if c.isalnum())
            if left and right and (
                left == right
                or _difflib.SequenceMatcher(None, left, right).ratio() >= 0.82
            ):
                return False, "repeat clarification forbidden"
        except Exception:
            pass
    if proposed_question and query:
        try:
            redundant, why = clarification_is_redundant(proposed_question, query)
            if redundant:
                return False, why
        except Exception:
            pass
    # No entities at all and no clear intent blocks meaningful execution.
    try:
        ents = list(entities) if entities is not None else detect_entities(query or "")
    except Exception:
        ents = []
    if not ents:
        return True, "no entities detected"
    return True, "missing blocks execution"

__all__ = [
    "HISTORY_TOOLS",
    "ResearchPlan",
    "SNAPSHOT_TOOLS",
    "_CURRENCY_WORD_RE_CLAR",
    "_GEO_WORD_RE",
    "_MACRO_INTENT_RE",
    "_METRIC_WORD_RE",
    "_RESEARCHABLE_DATA_REQUEST_RE",
    "_SOURCE_WORD_RE",
    "_TIMEFRAME_WORD_RE",
    "_TOOL_CATALOG_KEYS",
    "_URL_RE",
    "_WHO_WHAT_RE",
    "_build_research_plan",
    "_legacy_build_research_plan_removed",
    "build_research_plan",
    "clarification_asks_for_researchable_data",
    "clarification_is_redundant",
    "decompose_comparison_query",
    "is_researchable_comparison",
    "merge_clarification_context",
    "must_not_clarify",
    "plan_to_dict",
    "reconcile_judge_tools",
    "required_tools_for_query",
    "resolve_tool_plan",
    "should_clarify_generic",
]
