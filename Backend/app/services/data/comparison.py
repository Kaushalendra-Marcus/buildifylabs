"""Deterministic multi-entity, multi-metric historical comparison support.

Root cause of the NVIDIA-vs-AMD failure (and the earlier tech-startup vs
robotics-startup failure): the pipeline had

- no query decomposition (entities / metrics / historical period),
- only a 1-month Yahoo price series + a current-snapshot quoteSummary, which
  were charted as if they answered a 3-year historical question,
- no evidence model (entity / metric / value / unit / period / source /
  historical-vs-current),
- no period or comparison validation, and
- an unconditional visual guarantee that charted whatever arrived even at
  0% confidence.

This module is the deterministic core that fixes all of those without any
LLM arithmetic (specs/11 S2: the LLM narrates, code computes):

- :func:`decompose_comparison_query` -- entities, metrics, historical period.
- :func:`validate_historical_coverage` -- a 1-month / snapshot series can
  never satisfy a ~3-year request.
- :func:`validate_comparison` -- same metric / unit / period / frequency /
  definition, both entities present.
- :func:`compute_pct_change` / :func:`compute_comparison_stats` -- stock and
  revenue % changes plus a single shared profitability metric, with the
  formula and assumptions stated.
- :func:`comparison_confidence` / :func:`insufficient_reason` -- evidence
  completeness drives confidence; missing evidence blocks visualization.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known companies -> Yahoo symbols. The web-search STOCK_ALIASES map is the
# live lookup table; this mirror exists so decomposition never depends on the
# network and so tests can assert the exact failing query decomposes.
# ---------------------------------------------------------------------------
KNOWN_COMPANIES: Dict[str, str] = {
    "nvidia": "NVDA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "tesla": "TSLA",
    "byd": "BYDDY",
    "toyota": "TM",
    "toyota motor": "TM",
    "apple": "AAPL",
    "amazon": "AMZN",
    "nike": "NKE",
    "reliance": "RELIANCE.NS",
    "microsoft": "MSFT",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "intel": "INTC",
}

# Canonical display names (query-order output of detect_entities).
_DISPLAY_NAMES: Dict[str, str] = {
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "tesla": "Tesla",
    "byd": "BYD",
    "toyota": "Toyota",
    "toyota motor": "Toyota",
}

# Canonical metric keys required by the failing query.
METRIC_STOCK = "stock_performance"
METRIC_REVENUE = "revenue_growth"
METRIC_PROFIT = "profitability"

_STOCK_RE = re.compile(
    r"\b(stock|share|price|market\s*(performance|cap)?|ticker|nasdaq|nyse|"
    r"price\s*change|returns?)\b",
    re.IGNORECASE,
)
_REVENUE_RE = re.compile(r"\b(revenue|revenues|sales|turnover|revenue\s*growth)\b", re.IGNORECASE)
_PROFIT_RE = re.compile(
    r"\b(profit\w*|profitability|net\s*income|operating\s*margin|net\s*margin|"
    r"earnings|eps|ebitda|gross\s*margin)\b",
    re.IGNORECASE,
)

# "last 3 years" / "past three years" / "3-year" / "over 3 years" ...
_PERIOD_RE = re.compile(
    r"\b(?:last|past|previous|over(?:\s+the)?|trailing)\s+"
    r"(\d+|one|two|three|four|five|six|seven|ten)\s*[- ]?\s*"
    r"(years?|yrs?|y)\b"
    r"|\b(\d+)\s*[- ]?year\s*(comparison|history|trend|performance)?\b"
    r"|\b(3y|5y|10y)\b",
    re.IGNORECASE,
)
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "ten": 10,
}

_COMPARISON_RE = re.compile(
    r"\b(vs\.?|versus|compare|comparison|contrast|between|"
    r"which\s+(company|one)|stronger|better|best)\b",
    re.IGNORECASE,
)


def detect_entities(query: str) -> List[str]:
    """Detect known company entities plus generic 'X startup' style entities.

    Returns display names (e.g. ["NVIDIA", "AMD"]) in query order, deduped.
    """
    text = query or ""
    lowered = text.lower()
    found: List[str] = []
    seen: set[str] = set()

    # Known companies first (longest names first so "advanced micro devices"
    # wins over a bare "amd" overlap, "toyota motor" over "toyota").
    for name in sorted(KNOWN_COMPANIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            display = _DISPLAY_NAMES.get(
                name, name.upper() if len(name) <= 4 else name.title()
            )
            key = display.lower()
            if key not in seen:
                seen.add(key)
                found.append(display)

    # Generic "X startup" entities for the robotics-vs-tech failure:
    # "tech startup or robotics startup" -> ["tech startup", "robotics startup"].
    for match in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9&.\-]*\s+startup)\b", text, re.IGNORECASE):
        candidate = " ".join(match.group(1).split())
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            found.append(candidate)

    # Order by first appearance in the query for determinism.
    found.sort(key=lambda e: lowered.find(e.lower()) if lowered.find(e.lower()) >= 0 else 10**9)
    return found[:4]


def detect_metrics(query: str) -> List[str]:
    """Detect which of the required comparison metrics the query asks for."""
    text = query or ""
    metrics: List[str] = []
    if _STOCK_RE.search(text):
        metrics.append(METRIC_STOCK)
    if _REVENUE_RE.search(text):
        metrics.append(METRIC_REVENUE)
    if _PROFIT_RE.search(text):
        metrics.append(METRIC_PROFIT)
    return metrics


def detect_period_years(query: str) -> Optional[int]:
    """Detect an explicit historical window in years ("last 3 years" -> 3)."""
    text = query or ""
    match = _PERIOD_RE.search(text)
    if not match:
        return None
    for group in (match.group(1), match.group(3)):
        if group:
            raw = group.strip().lower()
            if raw.isdigit():
                return int(raw)
            if raw in _WORD_NUM:
                return int(_WORD_NUM[raw])
    short = (match.group(4) or "").lower()
    if short.endswith("y") and short[:-1].isdigit():
        return int(short[:-1])
    return None


def is_comparison_query(query: str) -> bool:
    return bool(_COMPARISON_RE.search(query or ""))


def symbol_for_entity(entity: str) -> Optional[str]:
    """Canonical Yahoo symbol for a known entity display name (None if unknown)."""
    if not entity:
        return None
    return KNOWN_COMPANIES.get(str(entity).strip().lower())


def build_research_plan(query: str) -> Dict[str, Any]:
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
    years = decomposed.get("period_years")
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
    return {
        "entities": entities,
        "metrics": metrics,
        "period_years": years,
        "period_label": decomposed.get("period_label"),
        "is_comparison": decomposed.get("is_comparison", False),
        "requires_history": decomposed.get("requires_history", False),
        "required_entities": entities,
        "required_metrics": metrics,
        "tasks": tasks,
    }


def is_researchable_comparison(query: str) -> bool:
    """True when the query is a historical multi-entity comparison whose
    entities, metrics, and period are all detected -- i.e. public data the
    agent must research itself rather than ask the user to supply."""
    try:
        plan = build_research_plan(query or "")
    except Exception:
        return False
    return bool(
        plan.get("is_comparison")
        and plan.get("requires_history")
        and len(plan.get("entities", []) or []) >= 2
        and len(plan.get("metrics", []) or []) >= 1
        and plan.get("period_years")
    )


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
    years = detect_period_years(query or "")
    return {
        "entities": entities,
        "metrics": metrics,
        "period_years": years,
        "period_label": f"{years}Y" if years else None,
        "is_comparison": is_comparison_query(query or "")
        or (len(entities) >= 2 and len(metrics) >= 1),
        "requires_history": years is not None and years >= 2,
    }


# ---------------------------------------------------------------------------
# Evidence model
# ---------------------------------------------------------------------------
@dataclass
class ComparisonEvidence:
    entity: str
    metric: str  # one of METRIC_* or a generic label
    value: float
    unit: str  # e.g. "USD", "USD_millions", "percent", "ratio"
    period_start: Optional[str] = None  # ISO date or label
    period_end: Optional[str] = None
    frequency: Optional[str] = None  # "daily" | "weekly" | "annual" | ...
    definition: Optional[str] = None  # e.g. "close", "annualTotalRevenue"
    geography: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    is_historical: bool = False


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    # Try ISO first, then common Yahoo label formats.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%b %d", "%b %d, %Y", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text[: len(fmt)] if fmt.startswith("%b") else text[:10], fmt)
            if fmt == "%b %d":
                # Month-day labels from the 1-month series carry no year:
                # treat as current-year dates (span stays ~30 days).
                return parsed.replace(year=date.today().year).date()
            return parsed.date()
        except (ValueError, OverflowError):
            continue
    try:
        return datetime.fromisoformat(text[:10]).date()
    except (ValueError, TypeError):
        return None


def _span_days(start: Any, end: Any) -> Optional[int]:
    s, e = _parse_date(start), _parse_date(end)
    if s is None or e is None:
        return None
    return abs((e - s).days)


def series_span_days(labels: Sequence[Any]) -> Optional[int]:
    """Day span covered by a chart label axis (None when unparseable)."""
    parsed = [_parse_date(label) for label in (labels or [])]
    parsed = [d for d in parsed if d is not None]
    if len(parsed) < 2:
        return None
    return abs((max(parsed) - min(parsed)).days)


def validate_historical_coverage(
    *,
    labels: Optional[Sequence[Any]] = None,
    period_start: Any = None,
    period_end: Any = None,
    requested_years: Optional[int] = None,
    values: Optional[Sequence[Any]] = None,
) -> Tuple[bool, str]:
    """True only when the retrieved data plausibly covers the requested window.

    A 1-month daily series (~30 points, "Aug 05 -> Sep 04"), a current
    snapshot (no dates at all), or unrelated dates all fail a 3-year request.
    The bar is deliberately lenient (60% of the requested span) so annual
    series (3-4 points over 3 years) pass while monthly/short series fail.
    """
    if not requested_years:
        return True, "no historical window requested"
    required_days = int(requested_years * 365 * 0.6)
    span: Optional[int] = None
    if labels:
        span = series_span_days(labels)
    if span is None and (period_start or period_end):
        span = _span_days(period_start, period_end)
    if span is None:
        # No dates at all -> current snapshot, never historical coverage.
        if values is not None and len(list(values or [])) >= 1 and not labels and not period_start and not period_end:
            return False, (
                "current snapshot has no historical dates; "
                f"cannot satisfy a {requested_years}-year request"
            )
        return False, (
            f"insufficient evidence: no dated coverage for a "
            f"{requested_years}-year request"
        )
    if span < required_days:
        return False, (
            f"insufficient evidence: data spans ~{span} days, "
            f"need ~{requested_years * 365} days ({requested_years} years)"
        )
    return True, f"covers ~{span} days for a {requested_years}-year request"


def validate_comparison(
    evidence: Sequence[ComparisonEvidence],
    *,
    expected_entities: Optional[Sequence[str]] = None,
    expected_metric: Optional[str] = None,
) -> Tuple[bool, str]:
    """Validate that a comparison is like-for-like before visualizing.

    Checks: >=2 distinct entities, same metric, same unit, same frequency,
    same definition, overlapping/same period. Anything else (total funding
    vs startup cost, current vs historical, one company's metric vs another
    company's unrelated metric, monthly vs 3-year) fails and must block the
    chart -- never label a mismatched visual as a comparison.
    """
    items = list(evidence or [])
    if len(items) < 2:
        return False, "insufficient evidence: need both companies, got fewer than 2 series"
    entities = {str(item.entity).strip().lower() for item in items if str(item.entity).strip()}
    if expected_entities:
        want = {str(e).strip().lower() for e in expected_entities if str(e).strip()}
        missing = want - entities
        if missing:
            return False, (
                f"insufficient evidence: missing entity {sorted(missing)}; "
                f"have {sorted(entities)}"
            )
    if len(entities) < 2:
        return False, "insufficient evidence: only one entity series for a two-company comparison"
    metrics = {str(item.metric).strip().lower() for item in items}
    if len(metrics) != 1:
        return False, f"definition mismatch: mixed metrics {sorted(metrics)}"
    if expected_metric and next(iter(metrics)) != str(expected_metric).strip().lower():
        return False, (
            f"metric mismatch: need {expected_metric}, have {sorted(metrics)}"
        )
    units = {str(item.unit).strip().lower() for item in items}
    if len(units) != 1:
        return False, f"unit mismatch: {sorted(units)}"
    freqs = {str(item.frequency or '').strip().lower() for item in items}
    freqs.discard("")
    if len(freqs) > 1:
        return False, f"frequency mismatch: {sorted(freqs)}"
    defs = {str(item.definition or '').strip().lower() for item in items}
    defs.discard("")
    if len(defs) > 1:
        return False, f"definition mismatch: {sorted(defs)}"
    # Period overlap: both must carry dates and they must overlap.
    dated = [item for item in items if item.period_start and item.period_end]
    if len(dated) < 2:
        return False, "insufficient evidence: missing comparable time periods"
    starts = [_parse_date(item.period_start) for item in dated]
    ends = [_parse_date(item.period_end) for item in dated]
    if any(d is None for d in starts + ends):
        return False, "insufficient evidence: unparseable time periods"
    latest_start = max(d for d in starts if d is not None)
    earliest_end = min(d for d in ends if d is not None)
    if latest_start > earliest_end:  # type: ignore[operator]
        return False, "period mismatch: non-overlapping time periods"
    return True, "comparison valid: same metric/unit/frequency/definition, overlapping period"


# ---------------------------------------------------------------------------
# Deterministic statistics (specs/11 S2: code computes, the LLM narrates)
# ---------------------------------------------------------------------------
def compute_pct_change(start: Any, end: Any) -> Optional[float]:
    """Percentage change from start to end, rounded to 2dp (None if undefined)."""
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if start_f == 0:
        return None
    return round((end_f - start_f) / abs(start_f) * 100, 2)


STATS_FORMULA = "pct_change = (end - start) / abs(start) * 100"
STATS_ASSUMPTIONS = (
    "Start/end are the first/last points of the same validated historical "
    "series for each company (same metric, unit, frequency, definition, "
    "overlapping period). Profitability uses ONE shared metric for every "
    "company: net profit margin = net income / revenue * 100, computed in "
    "code per fiscal year. Fiscal-year labels are preserved as reported; "
    "when fiscal years differ between companies the comparison basis is the "
    "latest comparable completed annual periods as labeled."
)


def compute_net_margins(
    revenues: Sequence[Any], incomes: Sequence[Any]
) -> List[Optional[float]]:
    """Per-year net profit margin % = net income / revenue * 100 (2dp).

    Position-aligned with the input series; None where undefined (zero or
    missing revenue). Deterministic -- the LLM never computes margins.
    """
    margins: List[Optional[float]] = []
    for revenue, income in zip(list(revenues or []), list(incomes or [])):
        try:
            revenue_f = float(revenue) if revenue is not None else None
            income_f = float(income) if income is not None else None
        except (TypeError, ValueError):
            margins.append(None)
            continue
        if revenue_f is None or income_f is None or revenue_f == 0:
            margins.append(None)
            continue
        margins.append(round(income_f / revenue_f * 100, 2))
    return margins


def compute_yearly_stats(
    labels: Sequence[Any], values: Sequence[Any]
) -> List[Dict[str, Any]]:
    """Year-by-year values with deterministic year-over-year % changes.

    Returns [{label, value, yoy_pct_change}] where the first year's change
    is None (no prior year). Labels are preserved verbatim (fiscal-year
    metadata survives into the answer and visuals).
    """
    out: List[Dict[str, Any]] = []
    paired = list(zip(list(labels or []), list(values or [])))
    for index, (label, value) in enumerate(paired):
        try:
            value_f = float(value) if value is not None else None
        except (TypeError, ValueError):
            value_f = None
        yoy: Optional[float] = None
        if index > 0:
            try:
                prior = float(paired[index - 1][1])
                yoy = compute_pct_change(prior, value_f) if value_f is not None else None
            except (TypeError, ValueError):
                yoy = None
        out.append({"label": label, "value": value_f, "yoy_pct_change": yoy})
    return out


def compute_comparison_stats(
    evidence_by_entity_metric: Dict[str, Dict[str, Tuple[Any, Any]]],
    latest_margins: Optional[Dict[str, Any]] = None,
    yearly: Optional[Dict[str, Any]] = None,
    period_basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute start/end/%-change per entity+metric deterministically.

    Input: {entity: {metric: (start_value, end_value)}}.
    Output: {entity: {metric: {start, end, pct_change}}, "winners": {...},
    "formula": ..., "assumptions": ...}. Winners are decided by highest
    %-change for stock performance and revenue growth; the profitability
    winner is the highest LATEST net profit margin when `latest_margins`
    is supplied (one comparable margin per company), else highest net-income
    %-change. Optional `yearly` carries deterministic year-by-year series
    (labels preserved) and `period_basis` documents fiscal-year alignment.
    Always stated, never LLM-computed.
    """
    out: Dict[str, Any] = {"entities": {}, "winners": {}, "formula": STATS_FORMULA, "assumptions": STATS_ASSUMPTIONS}
    for entity, metrics in (evidence_by_entity_metric or {}).items():
        out["entities"][entity] = {}
        for metric, pair in (metrics or {}).items():
            start, end = pair
            try:
                start_f = float(start) if start is not None else None
                end_f = float(end) if end is not None else None
            except (TypeError, ValueError):
                start_f, end_f = None, None
            pct = compute_pct_change(start_f, end_f) if start_f is not None and end_f is not None else None
            out["entities"][entity][metric] = {"start": start_f, "end": end_f, "pct_change": pct}

    def _winner(metric: str) -> Optional[str]:
        if metric == METRIC_PROFIT and latest_margins:
            scored_margins = [
                (entity, margin) for entity, margin in latest_margins.items()
                if isinstance(margin, (int, float))
            ]
            if scored_margins:
                return max(scored_margins, key=lambda kv: kv[1])[0]
        scored = [
            (entity, vals.get(metric, {}).get("pct_change"))
            for entity, vals in out["entities"].items()
            if isinstance(vals.get(metric), dict)
        ]
        scored = [(e, s) for e, s in scored if isinstance(s, (int, float))]
        if not scored:
            return None
        return max(scored, key=lambda kv: kv[1])[0]

    for metric in (METRIC_STOCK, METRIC_REVENUE, METRIC_PROFIT):
        winner = _winner(metric)
        if winner is not None:
            out["winners"][metric] = winner
    if latest_margins:
        out["latest_margins"] = dict(latest_margins)
    if yearly:
        out["yearly"] = yearly
    out["profitability_definition"] = "net profit margin = net income / revenue * 100"
    if period_basis:
        out["period_basis"] = period_basis
    return out


def comparison_confidence(
    *,
    entities_found: Sequence[str],
    entities_required: Sequence[str],
    metrics_found: Sequence[str],
    metrics_required: Sequence[str],
    historical_ok: bool,
    comparison_ok: bool,
    source_count: int = 0,
) -> float:
    """Evidence-driven confidence for multi-entity historical comparisons.

    Missing entities, missing metrics, failed historical coverage, or failed
    like-for-like validation all force 0.0 -- and 0.0 must block
    visualization (never chart at 0%).
    """
    want_e = {str(e).strip().lower() for e in (entities_required or []) if str(e).strip()}
    have_e = {str(e).strip().lower() for e in (entities_found or []) if str(e).strip()}
    want_m = {str(m).strip().lower() for m in (metrics_required or []) if str(m).strip()}
    have_m = {str(m).strip().lower() for m in (metrics_found or []) if str(m).strip()}
    if want_e and not want_e.issubset(have_e):
        return 0.0
    if want_m and not want_m.issubset(have_m):
        return 0.0
    if not historical_ok or not comparison_ok:
        return 0.0
    if source_count >= 4:
        return 0.85
    if source_count >= 2:
        return 0.65
    return 0.45


def insufficient_reason(
    *,
    query: str,
    entities: Sequence[str],
    metrics: Sequence[str],
    requested_years: Optional[int],
    historical_ok: bool,
    historical_detail: str = "",
    comparison_ok: bool = False,
    comparison_detail: str = "",
) -> str:
    """Human-readable missing-data statement for blocked comparisons."""
    bits = [f"Could not build the requested comparison for {query.strip()[:120]!r}."]
    if requested_years:
        bits.append(f"Requested window: last {requested_years} years.")
    bits.append(f"Entities detected: {list(entities) or 'none'}.")
    bits.append(f"Metrics detected: {list(metrics) or 'none'}.")
    if not historical_ok:
        bits.append(f"Historical data missing/insufficient. {historical_detail}".strip())
    if not comparison_ok:
        bits.append(f"Comparison not like-for-like. {comparison_detail}".strip())
    count = len(list(entities or []))
    scope = f"all {count} companies" if count > 2 else "BOTH companies"
    bits.append(
        "What is missing: validated multi-year price history AND annual "
        f"revenue/profitability history for {scope} over the same "
        "period (same metric, unit, frequency, definition)."
    )
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Figure metric labelling: prevents funding-vs-cost style false comparisons
# (the shared root cause with the tech-startup vs robotics-startup failure).
# ---------------------------------------------------------------------------
_FIGURE_METRIC_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("revenue", re.compile(r"\b(revenue|revenues|sales|turnover)\b", re.IGNORECASE)),
    ("funding", re.compile(r"\b(funding|funded|raised|raising|investment|valuation|financing)\b", re.IGNORECASE)),
    ("deal", re.compile(r"\b(sold\s+for|sold|acqui(red|sition)|deal)\b", re.IGNORECASE)),
    ("cost", re.compile(r"\b(cost|капитал|costs|expense|expenses|startup\s*cost|spend|spending)\b", re.IGNORECASE)),
    ("price", re.compile(r"\b(price|prices|share\s*price|close|closing)\b", re.IGNORECASE)),
    ("profit", re.compile(r"\b(profit\w*|margin|net\s*income|earnings|ebitda)\b", re.IGNORECASE)),
    ("market_cap", re.compile(r"\b(market\s*cap|marketcap)\b", re.IGNORECASE)),
]


def figure_metric_label(context: str) -> Optional[str]:
    """Metric cue for a snippet context (None when no cue present)."""
    for label, pattern in _FIGURE_METRIC_PATTERNS:
        if pattern.search(context or ""):
            return label
    return None


def figures_share_metric(figures: Sequence[Dict[str, Any]]) -> Tuple[bool, str]:
    """True when cited figures are comparable (same metric cue or uncured).

    Only blocks when two figures carry EXPLICITLY different cues (e.g.
    funding vs cost) -- uncured figures stay comparable for backward
    compatibility with existing qualitative answers.
    """
    labels = [figure_metric_label(str(fig.get("context", ""))) for fig in (figures or [])]
    # "deal" (sold for) and "funding" (raised) are both transaction values:
    # normalise to one bucket so "raised $300k vs sold for $1M" still compares.
    normalised = [{"deal": "funding"}.get(label, label) for label in labels]
    present = [label for label in normalised if label]
    if len(set(present)) <= 1:
        return True, "same or uncured metric"
    return False, f"metric mismatch across figures: {sorted(set(present))}"


def figure_entity_label(context: str, entities: Sequence[str]) -> Optional[str]:
    """Which queried entity (if any) a figure context mentions."""
    lowered = (context or "").lower()
    for entity in entities or []:
        if entity and str(entity).lower() in lowered:
            return str(entity)
    return None
