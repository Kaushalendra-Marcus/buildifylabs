"""Evidence model, validation, stats, completeness. Split from comparison.py; behavior unchanged."""

from __future__ import annotations

import logging
import re

from typing import Any, Dict, List, Optional, Sequence, Tuple
from dataclasses import dataclass, field
from datetime import date, datetime

from .symbols import METRIC_PROFIT, METRIC_REVENUE, METRIC_STOCK
from .timeframe import _FISCAL_YEAR_RE
from .currency import _ISO_CURRENCY_RE, evidence_currency
from .figures import is_figure_comparison_eligible

logger = logging.getLogger(__name__)



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
    # --- semantic binding (Phases 4/11): every numerical evidence item meant
    # for comparison should carry these. currency is an explicit ISO code
    # ("USD", "CNY", "JPY", ...) or None (explicitly unknown -- never the
    # generic string "currency"). scale is the magnitude multiplier the value
    # is expressed in (1 = single units). population/scope is the measured
    # cohort ("all stores", "US retail"). source_type is one of
    # "yahoo" | "fred" | "snippet" | "snapshot" | "web_research".
    # provenance is the free-text origin trail. comparison_eligible False
    # means "untyped / unattributed -- must never enter numerical comparison".
    currency: Optional[str] = None
    scale: Optional[float] = None
    population: Optional[str] = None
    source_type: Optional[str] = None
    provenance: Optional[str] = None
    comparison_eligible: bool = True


@dataclass
class TypedFigure:
    """A snippet figure with semantic binding (Phase 4/5, hardened H2).

    Raw extraction preserves the verbatim figure; semantic fields are filled
    by context analysis. comparison_eligible is False unless the FULL
    H2 contract holds (entity + specific metric + numeric value + unit +
    explicit ISO currency for money + definition + source_type) -- untyped
    "$202B" must never enter numerical comparison. The raw figure is always
    preserved for prose/context; eligibility only gates math/tables/bars.
    """

    text: str
    value: float
    unit: str  # "money" | "percent"
    context: str
    ref: int
    entity: Optional[str] = None
    metric: Optional[str] = None
    currency: Optional[str] = None
    scale: Optional[float] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    frequency: Optional[str] = None
    definition: Optional[str] = None
    geography: Optional[str] = None
    population: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    source_type: str = "snippet"
    comparison_eligible: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text, "value": self.value, "unit": self.unit,
            "context": self.context, "ref": self.ref,
            "entity": self.entity, "metric": self.metric,
            "currency": self.currency, "scale": self.scale,
            "period_start": self.period_start, "period_end": self.period_end,
            "frequency": self.frequency, "definition": self.definition,
            "geography": self.geography, "population": self.population,
            "source": self.source, "source_url": self.source_url,
            "source_type": self.source_type,
            "comparison_eligible": self.comparison_eligible,
        }

    def to_evidence(self, default_metric: str = "") -> Optional[ComparisonEvidence]:
        """Convert to ComparisonEvidence; None when not comparison-eligible.

        Hardened (H2/H4): re-validates the FULL eligibility contract here
        (not just the stored flag) so a hand-constructed eligible flag with
        generic metric / missing currency can never slip into math.
        """
        if not self.comparison_eligible:
            return None
        try:
            eligible, _ = is_figure_comparison_eligible(self)
        except Exception:
            eligible = self.comparison_eligible
        if not eligible:
            return None
        return ComparisonEvidence(
            entity=self.entity or "unknown",
            metric=self.metric or default_metric or "unknown",
            value=self.value,
            unit=self.currency or self.unit,
            period_start=self.period_start,
            period_end=self.period_end,
            frequency=self.frequency,
            definition=self.definition or self.metric,
            geography=self.geography,
            source=self.source,
            source_url=self.source_url,
            is_historical=bool(self.period_start and self.period_end),
            currency=self.currency,
            scale=self.scale,
            population=self.population,
            source_type=self.source_type,
            provenance=f"snippet [{self.ref}]: {self.context[:120]}",
            comparison_eligible=True,
        )


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
    # Fiscal labels ("FY2023", "fiscal 2023", "FY 2022") carry a real year:
    # map to Jan 1 of that year so fiscal-year evidence stays comparable
    # (previously these parsed as None -> "unparseable time periods" and
    # blocked every valid fiscal-year comparison).
    fiscal = _FISCAL_YEAR_RE.search(text)
    if fiscal:
        try:
            return date(int(fiscal.group(1) or fiscal.group(2)), 1, 1)
        except (TypeError, ValueError):
            pass
    bare_year = re.fullmatch(r"(19\d{2}|20\d{2})", text)
    if bare_year:
        try:
            return date(int(bare_year.group(1)), 1, 1)
        except (TypeError, ValueError):
            pass
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


def _distinct_years(labels: Optional[Sequence[Any]]) -> int:
    """Distinct calendar years across labels (annual observation coverage)."""
    years: set[int] = set()
    for label in list(labels or []):
        parsed = _parse_date(label)
        if parsed is not None:
            years.add(parsed.year)
    return len(years)


def validate_historical_coverage(
    *,
    labels: Optional[Sequence[Any]] = None,
    period_start: Any = None,
    period_end: Any = None,
    requested_years: Optional[int] = None,
    values: Optional[Sequence[Any]] = None,
    frequency: Optional[str] = None,
) -> Tuple[bool, str]:
    """True only when TEMPORAL SPAN *and* OBSERVATION COVERAGE satisfy the ask.

    Span alone never suffices: two snapshots ~3 years apart are a ~3-year
    span with only 2 observations, not a multi-year series. For annual
    requests the series must carry distinct calendar-year observations
    (min(requested_years, 3) distinct years for requests >= 2Y); sparse
    snapshots are reported as snapshots, never as complete series. Fiscal
    vs calendar and annual vs monthly mixes are flagged by callers via
    frequency/definition checks; here a stated monthly frequency can never
    satisfy a multi-year annual request with only 1-2 points.
    """
    if not requested_years:
        return True, "no historical window requested"
    if int(requested_years) < 2:
        # Sub-2Y asks need only a dated span; observation rule applies >= 2Y.
        required_days = int(int(requested_years) * 365 * 0.8)
        span: Optional[int] = None
        if labels:
            span = series_span_days(labels)
        if span is None and (period_start or period_end):
            span = _span_days(period_start, period_end)
        if span is None:
            return False, (
                f"insufficient evidence: no dated coverage for a "
                f"{requested_years}-year request"
            )
        if span < required_days:
            return False, (
                f"insufficient evidence: data spans ~{span} days, "
                f"need ~{int(requested_years) * 365} days ({requested_years} years)"
            )
        return True, f"covers ~{span} days for a {requested_years}-year request"
    try:
        n_values = len(list(values or [])) if values is not None else 0
    except Exception:
        n_values = 0
    n_labels = len(list(labels or [])) if labels else 0
    n_obs = min(n_values or n_labels, n_labels or n_values) if (n_values and n_labels) else (n_values or n_labels)
    distinct = _distinct_years(labels) if labels else 0
    required_obs = min(int(requested_years), 3)
    freq = str(frequency or "").strip().lower()
    sub_annual = freq in ("monthly", "month", "weekly", "week", "daily", "day")
    if sub_annual:
        # Sub-annual frequency needs proportionally more points, not 2.
        required_obs = max(required_obs, min(int(requested_years) * 2, 6))
    annual_series_ok = bool(labels and not sub_annual and distinct >= required_obs)
    # Annual observations cover calendar years: N annual points span N-1
    # elapsed years but represent N periods (2022+2023+2024 answers "3Y").
    # Two snapshots spanning enough days are still a snapshot pair, never
    # a series -- the observation rule below blocks them regardless.
    if annual_series_ok:
        required_days = int(max(1, requested_years - 1) * 365 * 0.7)
    else:
        required_days = int(requested_years * 365 * 0.7)
    span = None
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
    # Observation coverage: distinct dated observations must represent the
    # requested periods. Two endpoints spanning enough days are still a
    # snapshot pair, not a series.
    if labels and distinct < required_obs:
        return False, (
            f"insufficient evidence: only {distinct} distinct annual "
            f"observation(s) for a {requested_years}-year request "
            f"(need {required_obs}); sparse snapshots cannot satisfy history"
        )
    if n_obs and n_obs < 2:
        return False, (
            f"insufficient evidence: only {n_obs} observation(s) for a "
            f"{requested_years}-year request"
        )
    return True, f"covers ~{span} days with {distinct or n_obs} observation(s) for a {requested_years}-year request"


def validate_comparison(
    evidence: Sequence[ComparisonEvidence],
    *,
    expected_entities: Optional[Sequence[str]] = None,
    expected_metric: Optional[str] = None,
    fx_rates: Optional[Dict[str, float]] = None,
    enforce_currency: bool = True,
) -> Tuple[bool, str]:
    """Validate that a comparison is like-for-like before visualizing.

    Checks: >=2 distinct entities, same metric, same unit, same frequency,
    same definition, overlapping/same period, compatible currencies (same
    explicit code, or at least one side unknown, or an explicit deterministic
    FX conversion supplied), same geography/population when stated, and same
    historical/current status. Anything else (total funding vs startup
    cost, current vs historical, USD vs JPY without FX, one company's metric
    vs another company's unrelated metric, monthly vs 3-year) fails and must
    block the chart -- never label a mismatched visual as a comparison.

    enforce_currency=False skips ONLY the currency check, for the
    currency-invariant growth path (per-entity pct_change and net margins
    are unitless and valid across reporting currencies). Absolute-value
    outputs must always enforce it.
    """
    items = [item for item in (evidence or []) if getattr(item, "comparison_eligible", True)]
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
    # H5: explicit ISO units (USD/JPY/...) must agree for ABSOLUTE reads;
    # the legacy generic "currency"/"money" unit is a wildcard that defers
    # to the currency check below (unknown never blocks growth math, only
    # absolute reads with two KNOWN-different codes fail there). On the
    # currency-invariant growth path (enforce_currency=False) different
    # ISO codes are EXPECTED (USD vs CNY vs JPY growth % still compares)
    # and must not block here either.
    _GENERIC_UNITS = {"currency", "money", "currency:unknown"}
    _specific_units = {u for u in units if u not in _GENERIC_UNITS}
    _all_specific_are_iso = bool(_specific_units) and all(
        bool(_ISO_CURRENCY_RE.match(u.upper())) for u in _specific_units
    )
    if len(_specific_units) > 1 and not (not enforce_currency and _all_specific_are_iso):
        return False, f"unit mismatch: {sorted(units)}"
    if len(units) != 1 and len(_specific_units) == 1 and len(units) > 1:
        # Mixed generic + one specific code (e.g. fixture "currency" next
        # to live "usd"): allow here; the currency check decides absolutes.
        pass
    elif len(units) != 1 and not (not enforce_currency and _all_specific_are_iso):
        return False, f"unit mismatch: {sorted(units)}"
    # Currency: explicit codes must agree unless a deterministic FX
    # conversion is supplied. Unknown (None) on either side passes with an
    # assumption (callers record it) -- but never silently equates CNY/JPY.
    # Skipped only for the currency-invariant growth path (see docstring).
    fx_rates = fx_rates or {}
    known_currencies = {evidence_currency(item) for item in items}
    known_currencies.discard(None)
    if enforce_currency and len(known_currencies) > 1:
        codes = sorted(known_currencies)
        convertible = all(
            str(code).upper() in {str(k).upper() for k in fx_rates}
            or str(code).upper() == codes[0]
            for code in codes
        )
        if not convertible:
            return False, (
                f"currency mismatch: {codes} with no explicit FX conversion; "
                "absolute values in different currencies must not compare"
            )
    freqs = {str(item.frequency or '').strip().lower() for item in items}
    freqs.discard("")
    if len(freqs) > 1:
        return False, f"frequency mismatch: {sorted(freqs)}"
    defs = {str(item.definition or '').strip().lower() for item in items}
    defs.discard("")
    if len(defs) > 1:
        return False, f"definition mismatch: {sorted(defs)}"
    # Aggregation semantics: total vs average vs median vs snapshot are
    # different statistics and must never compare as like-for-like.
    stats = {
        str(
            getattr(item, "statistic", getattr(item, "aggregation", None)) or ""
        ).strip().lower()
        for item in items
    }
    stats.discard("")
    stats.discard("unknown")
    if len(stats) > 1:
        return False, f"aggregation mismatch: {sorted(stats)} (total/average/median must not compare)"
    # Geography / population-scope: stated values must agree across sides.
    geos = {str(item.geography or '').strip().lower() for item in items}
    geos.discard("")
    if len(geos) > 1:
        return False, f"geography mismatch: {sorted(geos)}"
    pops = {
        str(getattr(item, "population", None) or '').strip().lower()
        for item in items
    }
    pops.discard("")
    if len(pops) > 1:
        return False, f"population/scope mismatch: {sorted(pops)}"
    # Historical/current status: a current snapshot next to dated history is
    # never like-for-like (the exact "snapshot used as historical growth" bug).
    statuses = {bool(item.is_historical) for item in items}
    if len(statuses) > 1:
        return False, (
            "historical/current status mismatch: current snapshot values must "
            "not compare against historical series"
        )
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


def comparability_unknowns(evidence: Sequence["ComparisonEvidence"]) -> Dict[str, List[str]]:
    """Unknown metadata dimensions per entity (UNKNOWN stays UNKNOWN).

    Returns {dimension: [entity, ...]} for frequency/currency/definition/
    geography/population/period unknown on historical evidence. Callers must
    record these as assumptions and must not treat them as compatible when
    the dimension is necessary for the comparison (frequency/period for
    historical, currency for absolutes).
    """
    out: Dict[str, List[str]] = {
        "frequency": [], "currency": [], "definition": [],
        "geography": [], "population": [], "period": [],
    }
    for item in evidence or []:
        name = str(getattr(item, "entity", "") or "")
        if not str(getattr(item, "frequency", "") or "").strip():
            out["frequency"].append(name)
        if evidence_currency(item) is None and str(getattr(item, "unit", "") or "").strip().lower() in (
            "currency", "money", "currency:unknown", "price",
        ):
            out["currency"].append(name)
        if not str(getattr(item, "definition", "") or "").strip():
            out["definition"].append(name)
        if not str(getattr(item, "geography", "") or "").strip():
            out["geography"].append(name)
        if not str(getattr(item, "population", "") or "").strip():
            out["population"].append(name)
        if not getattr(item, "period_start", None) or not getattr(item, "period_end", None):
            out["period"].append(name)
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# Deterministic statistics (specs/11 S2: code computes, the LLM narrates)
# ---------------------------------------------------------------------------
def compute_pct_change(start: Any, end: Any) -> Optional[float]:
    """Simple end-to-end percentage change, rounded to 2dp (None if undefined).

    Explicitly NOT a CAGR: callers must label it as simple percentage
    change. None vs 0 vs NaN are distinguished (0 is valid, None/NaN
    missing, zero-base undefined -> None, never inf).
    """
    try:
        from app.services.data.canonical import simple_pct_change as _simple

        return _simple(start, end)
    except Exception:
        pass
    try:
        import math as _math

        if start is None or end is None:
            return None
        start_f, end_f = float(start), float(end)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (_math.isfinite(start_f) and _math.isfinite(end_f)):
        return None
    if start_f == 0:
        return None
    return round((end_f - start_f) / abs(start_f) * 100, 2)


def compute_cagr(start: Any, end: Any, periods: Any) -> Optional[float]:
    """Deterministic CAGR with explicit period semantics (percent, 2dp)."""
    try:
        from app.services.data.canonical import compute_cagr as _cagr

        return _cagr(start, end, periods)
    except Exception:
        return None


STATS_FORMULA = (
    "pct_change = (end - start) / abs(start) * 100 "
    "(simple end-to-end percentage change only; not CAGR)"
)
CAGR_FORMULA = "CAGR = (end/start)^(1/periods) - 1, in percent"
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
    revenues: Sequence[Any],
    incomes: Sequence[Any],
    revenue_labels: Optional[Sequence[Any]] = None,
    income_labels: Optional[Sequence[Any]] = None,
) -> List[Optional[float]]:
    """Per-year net profit margin % = net income / revenue * 100 (2dp).

    Joined by normalized period label when both label series are given
    (never pure position alignment across mismatched series); falls back
    to position alignment only when labels are absent or identical.
    Returns None where undefined (zero/missing revenue) -- never 0-filled,
    never silently truncated: length mismatches without labels yield None
    for the unmatched tail instead of shifted values. Deterministic.
    """
    rev = list(revenues or [])
    inc = list(incomes or [])
    if revenue_labels is not None or income_labels is not None:
        try:
            from app.services.data.canonical import strip_clarification_fragments as _s

            def _norm(label: Any) -> str:
                return " ".join(str(label or "").strip().split()).lower()

            if revenue_labels is not None and income_labels is not None:
                rev_labels = [_norm(l) for l in list(revenue_labels or [])]
                inc_labels = [_norm(l) for l in list(income_labels or [])]
                if rev_labels == inc_labels:
                    pass  # identical order: position path below is exact
                else:
                    # Join by period: explicit None gap where a period is
                    # missing on either side (never shift values).
                    rev_map = {l: v for l, v in zip(rev_labels, rev)}
                    inc_map = {l: v for l, v in zip(inc_labels, inc)}
                    margins: List[Optional[float]] = []
                    for label in rev_labels:
                        margins.append(
                            _margin_pair(
                                rev_map.get(label), inc_map.get(label)
                            )
                        )
                    return margins
            _ = _s
        except Exception:
            pass
    margins = []
    for revenue, income in zip(rev, inc):
        margins.append(_margin_pair(revenue, income))
    # Length mismatch without labels: pad the shorter side with None so no
    # value ever shifts position.
    if len(rev) != len(inc):
        margins.extend([None] * abs(len(rev) - len(inc)))
    return margins


def _margin_pair(revenue: Any, income: Any) -> Optional[float]:
    try:
        import math as _math

        if revenue is None or income is None:
            return None
        revenue_f = float(revenue)  # type: ignore[arg-type]
        income_f = float(income)  # type: ignore[arg-type]
        if not (_math.isfinite(revenue_f) and _math.isfinite(income_f)):
            return None
    except (TypeError, ValueError):
        return None
    if revenue_f == 0:
        return None
    return round(income_f / revenue_f * 100, 2)


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


# ---------------------------------------------------------------------------
# Evidence coverage: explicit requested x metrics x time-range requirements
# with retrieved / validated / unavailable states + partial-result policy.
# Single source of truth for answer scope, stats, confidence, visuals.
# ---------------------------------------------------------------------------
@dataclass
class EvidenceRequirement:
    entity: str
    metric: str
    time_range_label: Optional[str] = None


@dataclass
class EvidenceCoverage:
    """Canonical validated evidence state (one per request).

    requested: every entity x metric requirement from the plan.
    retrieved: requirements with raw evidence on hand.
    validated: requirements passing history + like-for-like.
    unavailable: requested minus validated, each with a reason.
    validated_entities / validated_metrics: subsets sufficient for partial.
    excluded_*: requested items dropped from the answer, with reasons.
    sufficient: >=2 validated entities sharing >=1 validated metric.
    partial: sufficient but not complete (some requested excluded).
    """

    requested: List[EvidenceRequirement] = field(default_factory=list)
    retrieved: List[EvidenceRequirement] = field(default_factory=list)
    validated: List[EvidenceRequirement] = field(default_factory=list)
    unavailable: List[Dict[str, Any]] = field(default_factory=list)
    validated_entities: List[str] = field(default_factory=list)
    validated_metrics: List[str] = field(default_factory=list)
    excluded_entities: List[Dict[str, Any]] = field(default_factory=list)
    excluded_metrics: List[Dict[str, Any]] = field(default_factory=list)
    sufficient: bool = False
    partial: bool = False
    complete: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": [r.__dict__ for r in self.requested],
            "retrieved_count": len(self.retrieved),
            "validated_count": len(self.validated),
            "unavailable": list(self.unavailable),
            "validated_entities": list(self.validated_entities),
            "validated_metrics": list(self.validated_metrics),
            "excluded_entities": list(self.excluded_entities),
            "excluded_metrics": list(self.excluded_metrics),
            "sufficient": self.sufficient,
            "partial": self.partial,
            "complete": self.complete,
        }


def build_evidence_coverage(
    *,
    entities: Sequence[str],
    metrics: Sequence[str],
    time_range_label: Optional[str] = None,
    per_entity_metric_ok: Optional[Dict[str, Dict[str, bool]]] = None,
    reasons: Optional[Dict[str, str]] = None,
) -> EvidenceCoverage:
    """Build the canonical coverage matrix from per-entity x metric validity.

    per_entity_metric_ok[entity][metric] True means retrieved AND validated.
    Missing entries mean unavailable (never zero-filled, never inferred).
    Partial policy: sufficient when >=2 entities share >=1 validated metric;
    otherwise insufficient and the affected comparison must BLOCK.
    """
    entities = [str(e) for e in (entities or []) if str(e).strip()]
    metrics = [str(m) for m in (metrics or []) if str(m).strip()]
    per_entity_metric_ok = per_entity_metric_ok or {}
    reasons = reasons or {}
    coverage = EvidenceCoverage()
    for entity in entities:
        for metric in metrics:
            req = EvidenceRequirement(
                entity=entity, metric=metric, time_range_label=time_range_label
            )
            coverage.requested.append(req)
            ok = bool((per_entity_metric_ok.get(entity) or {}).get(metric))
            if ok:
                coverage.retrieved.append(req)
                coverage.validated.append(req)
            else:
                coverage.unavailable.append({
                    "entity": entity, "metric": metric,
                    "reason": reasons.get(f"{entity}::{metric}")
                    or reasons.get(entity)
                    or f"no validated evidence for {entity} / {metric}",
                })
    # Validated subsets: entities/metrics with at least one validated cell.
    ent_ok: Dict[str, int] = {}
    met_ok: Dict[str, int] = {}
    for req in coverage.validated:
        ent_ok[req.entity] = ent_ok.get(req.entity, 0) + 1
        met_ok[req.metric] = met_ok.get(req.metric, 0) + 1
    # An entity counts as validated only when it has ALL requested metrics?
    # No -- partial metric policy: an entity is validated when it has at
    # least one validated metric; metric-level exclusion is tracked
    # separately so a missing profitability column does not drop revenue.
    coverage.validated_entities = sorted(ent_ok.keys())
    coverage.validated_metrics = sorted(met_ok.keys())
    # Common validated metrics across >=2 entities drive sufficiency.
    metric_entity_count: Dict[str, int] = {}
    for metric in metrics:
        ents = {
            req.entity for req in coverage.validated if req.metric == metric
        }
        metric_entity_count[metric] = len(ents)
    common = [m for m, c in metric_entity_count.items() if c >= 2]
    # Sufficiency: at least 2 validated entities sharing at least 1 metric,
    # or (non-comparison / single-metric degenerate) at least 1 validated
    # requirement when fewer than 2 entities were requested.
    if len(entities) >= 2:
        coverage.sufficient = len(common) >= 1 and len(coverage.validated_entities) >= 2
    elif len(entities) == 1:
        coverage.sufficient = len(coverage.validated) >= 1
    else:
        coverage.sufficient = False
    coverage.complete = (
        len(coverage.unavailable) == 0 and bool(coverage.requested)
    )
    coverage.partial = bool(coverage.sufficient and not coverage.complete)
    # Excluded: requested entities/metrics with zero validated cells.
    for entity in entities:
        if entity not in coverage.validated_entities:
            coverage.excluded_entities.append({
                "entity": entity,
                "reason": reasons.get(entity) or f"no validated evidence for {entity}",
            })
    for metric in metrics:
        if metric not in coverage.validated_metrics:
            coverage.excluded_metrics.append({
                "metric": metric,
                "reason": reasons.get(metric) or f"no validated evidence for {metric}",
            })
    return coverage


def coverage_exclusion_note(coverage: EvidenceCoverage) -> str:
    """Human-readable exclusion statement for partial answers.

    LEGACY: no longer on the user-facing path (check_research_completeness
    renders through canonical.exclusion_note_for instead, per the
    single-renderer rule). Kept for backward-compatible imports only.
    """
    bits: List[str] = []
    for item in coverage.excluded_entities:
        bits.append(f"{item.get('entity')} ({item.get('reason')})")
    for item in coverage.excluded_metrics:
        bits.append(f"{item.get('metric')} ({item.get('reason')})")
    if not bits:
        return ""
    return "Excluded from this comparison (insufficient validated evidence): " + "; ".join(bits) + "."


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

    Partial-result policy: a validated subset with >=2 comparable entities
    and >=1 common metric earns partial confidence (capped at 0.65), never
    0.0. Only an insufficient subset (<2 entities, no common metric, or
    failed validation for the subset) forces 0.0 -- and 0.0 must block
    visualization (never chart at 0%).
    """
    want_e = {str(e).strip().lower() for e in (entities_required or []) if str(e).strip()}
    have_e = {str(e).strip().lower() for e in (entities_found or []) if str(e).strip()}
    want_m = {str(m).strip().lower() for m in (metrics_required or []) if str(m).strip()}
    have_m = {str(m).strip().lower() for m in (metrics_found or []) if str(m).strip()}
    # Insufficient for ANY comparison: fewer than 2 validated entities or
    # no validated metric at all.
    if len(have_e) < 2 and len(want_e) >= 2:
        return 0.0
    if want_m and not have_m:
        return 0.0
    if not historical_ok or not comparison_ok:
        return 0.0
    full_e = (not want_e) or want_e.issubset(have_e)
    full_m = (not want_m) or want_m.issubset(have_m)
    if full_e and full_m:
        if source_count >= 4:
            return 0.85
        if source_count >= 2:
            return 0.65
        return 0.45
    # Partial subset: useful but explicitly incomplete -- cap below full.
    if source_count >= 2:
        return 0.65
    if source_count >= 1:
        return 0.45
    return 0.35


def assert_compatible_inputs(
    first: ComparisonEvidence,
    second: ComparisonEvidence,
    *,
    fx_rates: Optional[Dict[str, float]] = None,
) -> Tuple[bool, str]:
    """Pre-calculation guard: two evidence items may enter pct_change /
    margin math only when semantically compatible (same metric, definition,
    unit, frequency, compatible currency, same geography/population scope,
    same historical status, overlapping period, finite numerics). A
    calculator must NEVER discover meaning -- this assertion runs BEFORE
    any arithmetic. Returns (ok, reason); the full per-dimension verdict
    lives in validate_calculation_inputs(). Pure.
    """
    try:
        verdict = validate_calculation_inputs(first, second, fx_rates=fx_rates)
        if not verdict.get("ok"):
            return False, str(verdict.get("reason", "incompatible"))
    except Exception as exc:
        return False, f"incompatible calculation inputs: {exc}"
    ok, detail = validate_comparison(
        [first, second], fx_rates=fx_rates,
    )
    if not ok:
        return False, f"incompatible calculation inputs: {detail}"
    return True, "calculation inputs compatible"


def evidence_driven_confidence(
    model_confidence: Any,
    *,
    plan: Optional[Any] = None,
    entities_found: Optional[Sequence[str]] = None,
    metrics_found: Optional[Sequence[str]] = None,
    historical_ok: bool = True,
    comparison_ok: bool = True,
    row_count: int = 0,
    source_count: int = 0,
    snippet_count: int = 0,
    provider_count: int = 0,
    has_structured: bool = False,
) -> float:
    """Deterministic evidence-quality cap over the LLM's subjective signal.

    For comparison queries the comparison_confidence() verdict is the cap
    (missing entities/metrics, failed history, or failed like-for-like all
    force 0.0); otherwise a generic evidence cap (strong >= 0.90, thin >=
    0.65, nothing >= 0.35). The model's number survives below the cap and is
    NEVER lifted above it -- many snippets with incompatible numbers cannot
    produce high confidence. A BLOCKED historical comparison always yields
    0.0 here. Pure.
    """
    try:
        model_value = float(model_confidence)
    except (TypeError, ValueError):
        model_value = 0.0
    required_entities: Sequence[str] = []
    required_metrics: Sequence[str] = []
    is_comparison_plan = False
    requires_history_plan = False
    if isinstance(plan, dict):
        required_entities = list(
            plan.get("required_entities", plan.get("entities", [])) or []
        )
        required_metrics = list(
            plan.get("required_metrics", plan.get("metrics", [])) or []
        )
        is_comparison_plan = bool(
            plan.get("is_comparison", plan.get("comparison_requested", False))
        )
        requires_history_plan = bool(plan.get("requires_history", False))
    # The strict entity/metric-exact verdict governs HISTORICAL comparisons
    # (structured multi-year evidence is mandatory there). Qualitative
    # snippet comparisons use the generic evidence cap instead: cited prose
    # is their evidence, and a missing Yahoo series must not zero them.
    if is_comparison_plan and requires_history_plan and required_entities:
        cap = comparison_confidence(
            entities_found=list(entities_found or []),
            entities_required=list(required_entities),
            metrics_found=list(metrics_found or []),
            metrics_required=list(required_metrics),
            historical_ok=historical_ok,
            comparison_ok=comparison_ok,
            source_count=source_count,
        )
    else:
        if (
            row_count >= 3
            or snippet_count >= 3
            or provider_count >= 2
            or has_structured
        ):
            cap = 0.90
        elif row_count >= 1 or snippet_count >= 1:
            cap = 0.65
        else:
            cap = 0.35
    try:
        return round(min(model_value, cap), 2)
    except (TypeError, ValueError):
        return cap


def check_entity_completeness(
    expected_entities: Sequence[str],
    found_entities: Sequence[str],
) -> Tuple[bool, List[str]]:
    """True + [] when every expected entity is present; else False + missing.

    Used by every research stage so a 3-company ask never silently continues
    with two. Pure.
    """
    want = {str(e).strip().lower() for e in (expected_entities or []) if str(e).strip()}
    have = {str(e).strip().lower() for e in (found_entities or []) if str(e).strip()}
    missing = sorted(want - have)
    return (len(missing) == 0, missing)


def build_trace(
    *,
    query: str,
    plan: Optional[Any] = None,
    tools_requested: Optional[Sequence[str]] = None,
    tools_executed: Optional[Sequence[str]] = None,
    tool_results: Optional[Dict[str, Any]] = None,
    missing_evidence: Optional[Sequence[str]] = None,
    comparison_gate: Optional[Dict[str, Any]] = None,
    calculated_stats: Optional[Any] = None,
    visual_decision: Optional[Any] = None,
    final_confidence: Optional[Any] = None,
    # --- runtime-trace hardening (Phase H15): explicit contract fields ---
    planned_tools: Optional[Sequence[str]] = None,
    actually_executed_tools: Optional[Sequence[str]] = None,
    missing_entities: Optional[Sequence[str]] = None,
    missing_metrics: Optional[Sequence[str]] = None,
    completeness: Optional[Dict[str, Any]] = None,
    entities: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[str]] = None,
    time_range: Optional[Any] = None,
) -> Dict[str, Any]:
    """Structured per-request trace for observability (no secrets/PII beyond
    the query text itself; never log API keys, tokens, or user data rows).

    Runtime-trace contract (Phase H15): every production diagnosis must be
    possible from this dict alone -- QUERY, PLAN, ENTITIES, METRICS,
    TIME_RANGE, REQUIRED_TOOLS, PLANNED_TOOLS, ACTUALLY_EXECUTED_TOOLS,
    TOOL_RESULTS, MISSING_ENTITIES, MISSING_METRICS, COMPLETENESS,
    COMPARISON_GATE, BLOCKED_REASON, CALCULATED_STATS, VISUAL_DECISION,
    FINAL_CONFIDENCE. All values are JSON-safe scalars/lists/dicts; raw
    rows, API keys, tokens, and passwords are never included (callers must
    pass counts, never payloads). Pure.
    """
    gate = comparison_gate or {}
    plan_dict = plan if isinstance(plan, dict) else {}
    plan_entities = list(entities if entities is not None else plan_dict.get("entities", []) or [])
    plan_metrics = list(metrics if metrics is not None else plan_dict.get("metrics", []) or [])
    plan_time_range = time_range if time_range is not None else plan_dict.get("time_range")
    if hasattr(plan_time_range, "to_dict"):
        try:
            plan_time_range = plan_time_range.to_dict()  # type: ignore[union-attr]
        except Exception:
            plan_time_range = str(plan_time_range)
    required_tools = list(plan_dict.get("required_tools", []) or [])
    # PLANNED_TOOLS defaults to the explicit arg, then tools_requested (legacy).
    planned = list(planned_tools if planned_tools is not None else (tools_requested or []))
    executed = list(
        actually_executed_tools if actually_executed_tools is not None else (tools_executed or [])
    )
    trace = {
        "query": str(query or "")[:300],
        "plan": {
            "entities": plan_entities,
            "metrics": plan_metrics,
            "time_range": plan_time_range,
            "requires_history": bool(plan_dict.get("requires_history", False)),
            "required_tools": required_tools,
        },
        # Explicit top-level aliases so `trace["entities"]` works without
        # digging into `trace["plan"]`.
        "entities": plan_entities,
        "metrics": plan_metrics,
        "time_range": plan_time_range,
        "required_tools": required_tools,
        "planned_tools": planned,
        "tools_requested": list(tools_requested or planned),
        "actually_executed_tools": executed,
        "tools_executed": executed,
        "tool_results": dict(tool_results or {}),
        "missing_entities": list(missing_entities or []),
        "missing_metrics": list(missing_metrics or []),
        "missing_evidence": list(missing_evidence or []),
        "completeness": dict(completeness or {}),
        "comparison_gate": {
            "applies": bool(gate.get("applies")),
            "blocked": bool(gate.get("blocked")),
            "blocked_reason": str(gate.get("blocked_reason", "") or "")[:500],
            "historical_ok": gate.get("historical_ok"),
            "comparison_ok": gate.get("comparison_ok"),
        },
        "blocked_reason": str(gate.get("blocked_reason", "") or "")[:500],
        "calculated_stats": bool(calculated_stats),
        "visual_decision": visual_decision,
        "final_confidence": final_confidence,
    }
    return trace


def format_runtime_trace(trace: Dict[str, Any]) -> str:
    """One-line safe log rendering of a build_trace() dict.

    Contains only the trace contract fields (no payloads, no secrets).
    Use for `logger.info("RUNTIME TRACE %s", format_runtime_trace(trace))`.
    Pure.
    """
    try:
        import json as _json

        safe = {
            "query": str((trace or {}).get("query", ""))[:120],
            "entities": (trace or {}).get("entities"),
            "metrics": (trace or {}).get("metrics"),
            "time_range": (trace or {}).get("time_range"),
            "required_tools": (trace or {}).get("required_tools"),
            "planned_tools": (trace or {}).get("planned_tools"),
            "actually_executed_tools": (trace or {}).get("actually_executed_tools"),
            "tool_results": (trace or {}).get("tool_results"),
            "missing_entities": (trace or {}).get("missing_entities"),
            "missing_metrics": (trace or {}).get("missing_metrics"),
            "completeness": (trace or {}).get("completeness"),
            "comparison_gate": (trace or {}).get("comparison_gate"),
            "blocked_reason": str((trace or {}).get("blocked_reason", ""))[:200],
            "calculated_stats": (trace or {}).get("calculated_stats"),
            "visual_decision": (trace or {}).get("visual_decision"),
            "final_confidence": (trace or {}).get("final_confidence"),
        }
        return _json.dumps(safe, default=str)
    except Exception:
        return "runtime trace unavailable"


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


def validate_calculation_inputs(
    first: "ComparisonEvidence",
    second: "ComparisonEvidence",
    *,
    fx_rates: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Structured pre-calculation compatibility verdict (Phase H4).

    The calculator must NEVER decide meaning: this runs BEFORE any
    arithmetic and returns {"ok": bool, "reason": str, "checks": {...}}
    with one flag per contract dimension:

    - compatible_entity (two distinct entities)
    - compatible_metric, compatible_definition, compatible_unit
    - compatible_currency (same ISO or explicit deterministic FX supplied;
      unknown on either side passes with an assumption note recorded by the
      caller -- only two KNOWN-DIFFERENT codes fail)
    - compatible_period (overlapping dated periods), compatible_frequency
    - compatible_scope (geography/population), compatible_status
      (historical vs current), valid_numeric_value (finite numbers)

    Never raises; never converts currencies or scales silently. Callers
    must refuse to calculate when ok is False. Pure.
    """
    checks: Dict[str, Any] = {}
    try:
        entities = [str(getattr(first, "entity", "") or "").strip().lower(),
                    str(getattr(second, "entity", "") or "").strip().lower()]
        checks["compatible_entity"] = (
            bool(entities[0] and entities[1]) and entities[0] != entities[1]
        )
        checks["compatible_metric"] = (
            str(getattr(first, "metric", "") or "").strip().lower()
            == str(getattr(second, "metric", "") or "").strip().lower()
            and bool(str(getattr(first, "metric", "") or "").strip())
        )
        first_def = str(getattr(first, "definition", "") or "").strip().lower()
        second_def = str(getattr(second, "definition", "") or "").strip().lower()
        checks["compatible_definition"] = (
            (not first_def or not second_def or first_def == second_def)
            and checks["compatible_metric"]
        )
        # Unit: explicit currency codes compare as units too (USD vs JPY
        # fails here AND in the currency check -- defense in depth). The
        # legacy generic "currency" unit passes here and defers to the
        # currency check below.
        first_unit = str(getattr(first, "unit", "") or "").strip().lower()
        second_unit = str(getattr(second, "unit", "") or "").strip().lower()
        checks["compatible_unit"] = bool(first_unit and first_unit == second_unit)
        first_cur = evidence_currency(first)
        second_cur = evidence_currency(second)
        if first_cur and second_cur and first_cur != second_cur:
            rates = {str(k).upper(): float(v) for k, v in (fx_rates or {}).items()}
            checks["compatible_currency"] = (
                first_cur.upper() in rates and second_cur.upper() in rates
            )
        else:
            checks["compatible_currency"] = True
        first_freq = str(getattr(first, "frequency", "") or "").strip().lower()
        second_freq = str(getattr(second, "frequency", "") or "").strip().lower()
        checks["compatible_frequency"] = (
            (not first_freq or not second_freq or first_freq == second_freq)
        )
        # Period overlap.
        try:
            start_a, end_a = _parse_date(getattr(first, "period_start", None)), _parse_date(
                getattr(first, "period_end", None)
            )
            start_b, end_b = _parse_date(getattr(second, "period_start", None)), _parse_date(
                getattr(second, "period_end", None)
            )
            if start_a and end_a and start_b and end_b:
                checks["compatible_period"] = max(start_a, start_b) <= min(end_a, end_b)
            else:
                checks["compatible_period"] = False
        except Exception:
            checks["compatible_period"] = False
        first_geo = str(getattr(first, "geography", "") or "").strip().lower()
        second_geo = str(getattr(second, "geography", "") or "").strip().lower()
        first_pop = str(getattr(first, "population", "") or "").strip().lower()
        second_pop = str(getattr(second, "population", "") or "").strip().lower()
        checks["compatible_scope"] = (
            (not first_geo or not second_geo or first_geo == second_geo)
            and (not first_pop or not second_pop or first_pop == second_pop)
        )
        checks["compatible_status"] = (
            bool(getattr(first, "is_historical", False))
            == bool(getattr(second, "is_historical", False))
        )
        import math as _math

        try:
            first_v = float(getattr(first, "value", None))  # type: ignore[arg-type]
            second_v = float(getattr(second, "value", None))  # type: ignore[arg-type]
            checks["valid_numeric_value"] = bool(
                _math.isfinite(first_v) and _math.isfinite(second_v)
            )
        except (TypeError, ValueError):
            checks["valid_numeric_value"] = False
        ok = all(bool(v) for v in checks.values())
        failing = sorted(k for k, v in checks.items() if not v)
        reason = (
            "calculation inputs compatible"
            if ok else f"incompatible calculation inputs: {', '.join(failing)}"
        )
        return {"ok": ok, "reason": reason, "checks": checks}
    except Exception as exc:
        return {"ok": False, "reason": f"validation failed: {exc}", "checks": checks}


def check_research_completeness(
    plan: Any,
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    """Hard deterministic completeness verdict (Phase H6).

    Verifies the 9-point contract for the canonical ResearchPlan:

    1. every required entity exists in evidence
    2. every required metric exists per entity
    3. required tools actually produced evidence
    4. historical coverage is sufficient for the requested window
    5. evidence is comparison-eligible (like-for-like validation)
    6. periods overlap across entities
    7. units/currencies are compatible (or explicit FX supplied)
    8. definitions match across entities
    9. enough observations exist (>=2 dated points per entity+metric)

    `evidence` shape (all optional): {
      "price_history": [...], "financial_history": [...],
      "market_data": [...], "fundamentals": [...],
      "required_tools_present": [...],  # tools that actually yielded evidence
      "fx_rates": {...},
    }

    Returns {
      "comparison_complete": bool,
      "per_entity": {entity: {metric: bool, ...}},
      "missing_entities": [...], "missing_metrics": [...],
      "missing": [...human-readable...],
      "historical_ok": bool, "historical_detail": str,
      "comparison_ok": bool, "comparison_detail": str,
    }

    Never silently drops a company: a 3-company ask with 2 present yields
    comparison_complete False with the missing company named. Pure.
    """
    plan_dict = dict(plan) if isinstance(plan, dict) else {}
    required_entities: List[str] = list(
        plan_dict.get("required_entities", plan_dict.get("entities", [])) or []
    )
    required_metrics: List[str] = list(
        plan_dict.get("required_metrics", plan_dict.get("metrics", [])) or []
    )
    required_tools: List[str] = list(plan_dict.get("required_tools", []) or [])
    requested_years = plan_dict.get("period_years")
    evidence = dict(evidence or {})
    price_history = list(evidence.get("price_history") or [])
    financial_history = list(evidence.get("financial_history") or [])
    fx_rates = dict(evidence.get("fx_rates") or {})

    per_entity: Dict[str, Dict[str, bool]] = {}
    missing: List[str] = []
    missing_entities: List[str] = []
    missing_metrics: List[str] = []

    def _has_price(entity: str) -> bool:
        key = str(entity).strip().lower()
        return any(
            isinstance(item, dict)
            and str(item.get("entity", "")).strip().lower() == key
            and (item.get("values") or (item.get("revenue", {}) or {}).get("values"))
            or (
                isinstance(item, dict)
                and str(item.get("entity", "")).strip().lower() == key
                and item.get("values") and item.get("labels")
            )
            for item in price_history
        )

    def _block_values(entity: str, block: str) -> list:
        key = str(entity).strip().lower()
        for item in financial_history:
            if not isinstance(item, dict):
                continue
            if str(item.get("entity", "")).strip().lower() != key:
                continue
            chunk = (item.get(block, {}) or {})
            if isinstance(chunk, dict) and chunk.get("values"):
                return list(chunk.get("values") or [])
        return []

    def _block_labels(entity: str, block: str) -> list:
        key = str(entity).strip().lower()
        for item in financial_history:
            if not isinstance(item, dict):
                continue
            if str(item.get("entity", "")).strip().lower() != key:
                continue
            chunk = (item.get(block, {}) or {})
            if isinstance(chunk, dict) and chunk.get("values"):
                return list(chunk.get("labels") or [])
        return []

    # 1+2: per-entity x per-metric presence (>=2 dated points where history
    # is required; >=1 point otherwise).
    needs_history = bool(plan_dict.get("requires_history"))
    for entity in required_entities:
        per_entity.setdefault(entity, {})
        for metric in required_metrics:
            ok = False
            if metric == METRIC_STOCK:
                items = [
                    item for item in price_history
                    if isinstance(item, dict)
                    and str(item.get("entity", "")).strip().lower()
                    == str(entity).strip().lower()
                    and item.get("values") and item.get("labels")
                ]
                ok = any(len(list(item.get("values") or [])) >= (2 if needs_history else 1) for item in items)
            elif metric == METRIC_REVENUE:
                ok = len(_block_values(entity, "revenue")) >= (2 if needs_history else 1)
            elif metric == METRIC_PROFIT:
                ok = len(_block_values(entity, "net_income")) >= (2 if needs_history else 1)
            else:
                ok = False
            per_entity[entity][metric] = bool(ok)
            if not ok:
                missing.append(f"{entity}: {metric} missing")
                if metric not in missing_metrics:
                    missing_metrics.append(metric)

    # Entities with nothing at all are missing entities.
    for entity in required_entities:
        row = per_entity.get(entity, {})
        if row and not any(row.values()):
            if entity not in missing_entities:
                missing_entities.append(entity)
        elif not row and required_metrics:
            if entity not in missing_entities:
                missing_entities.append(entity)
    # Also: any required entity absent from every evidence list.
    have_entities = set()
    for lst in (price_history, financial_history):
        for item in lst:
            if isinstance(item, dict) and item.get("entity"):
                name = str(item.get("entity", "")).strip()
                if name:
                    have_entities.add(name.lower())
    for entity in required_entities:
        if str(entity).strip().lower() not in have_entities and entity not in missing_entities:
            missing_entities.append(entity)

    # 3: required tools actually produced evidence. Only CORE history
    # tools block the comparison (market_history for stock, financial_history
    # for revenue/profitability, market for non-historical stock):
    # fundamentals (current scale context) and snippets (explanation) are
    # recorded as missing context but never block the chart on their own --
    # otherwise a valid 3Y Yahoo comparison without fundamentals text would
    # wrongly stay chartless.
    _CORE_TOOLS = {"market_history", "financial_history", "market"}
    present_tools: List[str] = list(evidence.get("required_tools_present") or [])
    if not present_tools:
        # Derive from payload presence when the caller did not state it.
        if price_history:
            present_tools.append("market_history")
        if financial_history:
            present_tools.append("financial_history")
        if evidence.get("market_data"):
            present_tools.append("market")
        if evidence.get("fundamentals"):
            present_tools.append("fundamentals")
        if evidence.get("snippets") or evidence.get("web_snippet_count"):
            present_tools.append("snippets")
        if evidence.get("news_context"):
            present_tools.append("snippets")
    core_required = [t for t in required_tools if t in _CORE_TOOLS]
    # Historical stock/revenue intent implies its history tool even when
    # the plan's required_tools list predates the call (defense in depth).
    if needs_history:
        if METRIC_STOCK in required_metrics and "market_history" not in core_required:
            core_required.append("market_history")
        if (METRIC_REVENUE in required_metrics or METRIC_PROFIT in required_metrics) and "financial_history" not in core_required:
            core_required.append("financial_history")
    for tool in required_tools:
        if tool not in present_tools:
            if tool in _CORE_TOOLS:
                missing.append(f"required tool produced no evidence: {tool}")
            else:
                # Context-only gap: visible in trace, not a completeness fail.
                missing.append(f"context tool produced no evidence: {tool} (non-blocking)")

    # 4: historical coverage per series.
    historical_ok = True
    historical_bits: List[str] = []
    if requested_years and needs_history:
        if METRIC_STOCK in required_metrics:
            for item in price_history:
                ok, detail = validate_historical_coverage(
                    labels=list(item.get("labels") or []),
                    period_start=item.get("period_start"),
                    period_end=item.get("period_end"),
                    requested_years=requested_years,
                    values=item.get("values"),
                )
                if not ok:
                    historical_ok = False
                    historical_bits.append(f"{item.get('entity')}: {detail}")
        for block, metric in (("revenue", METRIC_REVENUE), ("net_income", METRIC_PROFIT)):
            if metric in required_metrics:
                for entity in required_entities:
                    labels = _block_labels(entity, block)
                    values = _block_values(entity, block)
                    if len(values) < 2:
                        historical_ok = False
                        historical_bits.append(f"{entity}: need 2+ annual {block} points")
    historical_detail = " ".join(historical_bits)

    # 5-9: like-for-like validation across entities (metric/unit/period/
    # frequency/definition/currency/scope/status + overlap + observations).
    comparison_ok = True
    comparison_bits: List[str] = []
    if required_entities and required_metrics:
        # Build one ComparisonEvidence per entity per metric from the raw
        # series tails (same construction the gate uses) and validate.
        for metric in required_metrics:
            group: List[ComparisonEvidence] = []
            for entity in required_entities:
                if metric == METRIC_STOCK:
                    series = next(
                        (item for item in price_history
                         if isinstance(item, dict)
                         and str(item.get("entity", "")).strip().lower()
                         == str(entity).strip().lower()
                         and item.get("values") and item.get("labels")),
                        None,
                    )
                    if series is None:
                        continue
                    vals = list(series.get("values") or [])
                    labs = list(series.get("labels") or [])
                    try:
                        group.append(ComparisonEvidence(
                            entity=str(entity), metric=metric, value=float(vals[-1]),
                            unit=str(series.get("currency", "price") or "price"),
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency=str(series.get("frequency", "weekly") or "weekly"),
                            definition=str(series.get("metric", "close") or "close"),
                            source="evidence", is_historical=True,
                        ))
                    except (TypeError, ValueError):
                        continue
                else:
                    block = "revenue" if metric == METRIC_REVENUE else "net_income"
                    want_metric = "annualTotalRevenue" if metric == METRIC_REVENUE else "annualNetIncome"
                    series = next(
                        (item for item in financial_history
                         if isinstance(item, dict)
                         and str(item.get("entity", "")).strip().lower()
                         == str(entity).strip().lower()
                         and isinstance(item.get(block, {}), dict)
                         and (item.get(block, {}) or {}).get("values")),
                        None,
                    )
                    if series is None:
                        continue
                    chunk = (series.get(block, {}) or {})
                    vals = list(chunk.get("values") or [])
                    labs = list(chunk.get("labels") or [])
                    if len(vals) < 2:
                        continue
                    currency = str(
                        chunk.get("currency", "") or series.get("currency", "") or ""
                    ).strip().upper() or None
                    try:
                        group.append(ComparisonEvidence(
                            entity=str(entity), metric=metric, value=float(vals[-1]),
                            unit=currency or "currency",
                            period_start=str(labs[0]) if labs else None,
                            period_end=str(labs[-1]) if labs else None,
                            frequency="annual", definition=str(chunk.get("metric", want_metric)),
                            source="evidence", is_historical=True, currency=currency,
                            source_type="evidence",
                        ))
                    except (TypeError, ValueError):
                        continue
            if len(group) >= 2:
                ok, detail = validate_comparison(
                    group, expected_entities=required_entities,
                    expected_metric=metric, fx_rates=fx_rates,
                    enforce_currency=False,
                )
                if not ok:
                    comparison_ok = False
                    comparison_bits.append(f"{metric}: {detail}")
                # Absolute-currency transparency: known-different codes fail
                # absolute reads (growth % still valid); record it.
                known = {evidence_currency(item) for item in group}
                known.discard(None)
                if len(known) > 1:
                    comparison_bits.append(
                        f"{metric}: absolute values in {sorted(known)} do not "
                        "compare without explicit FX (growth % does)"
                    )
            elif len(required_entities) >= 2:
                comparison_ok = False
                comparison_bits.append(f"{metric}: fewer than 2 entity series")
    comparison_detail = " ".join(comparison_bits)

    # Any per-entity gap fails the strict whole comparison (never silent
    # 2-of-3 as complete). Partial-result policy lives alongside: a
    # validated subset with >=2 comparable entities sharing >=1 metric is
    # sufficient for a partial answer with explicit exclusions.
    any_gap = any(
        not present for row in per_entity.values() for present in row.values()
    )
    missing_tool = any(
        tool not in present_tools for tool in core_required
    ) if core_required else False
    comparison_complete = bool(
        required_entities
        and required_metrics
        and not any_gap
        and not missing_tool
        and historical_ok
        and comparison_ok
    )
    # Canonical coverage matrix (single source of truth for partial scope).
    try:
        _reasons: Dict[str, str] = {}
        for item in (missing or []):
            _reasons.setdefault(str(item)[:120], str(item)[:200])
        # Per-metric reasons from the detail strings.
        _time_label = None
        try:
            _time_label = plan_dict.get("period_label")
        except Exception:
            _time_label = None
        coverage = build_evidence_coverage(
            entities=required_entities,
            metrics=required_metrics,
            time_range_label=_time_label,
            per_entity_metric_ok=per_entity,
            reasons={e: f"no validated evidence for {e}" for e in missing_entities},
        )
    except Exception:
        coverage = EvidenceCoverage()
    # Single-renderer rule: the user-facing exclusion note always renders
    # through canonical.exclusion_note_for (plain names, never raw list
    # repr, never a second ad-hoc format). coverage_exclusion_note() stays
    # defined as legacy but is no longer on the user-facing path.
    try:
        from app.services.data.canonical import exclusion_note_for as _excl_for

        _exclusion_note = _excl_for(
            {
                "excluded_entities": [
                    {"entity": item.get("entity")}
                    for item in coverage.excluded_entities
                ],
                "excluded_metrics": [
                    {"metric": item.get("metric")}
                    for item in coverage.excluded_metrics
                ],
            }
        )
    except Exception:
        _exclusion_note = coverage_exclusion_note(coverage)
    return {
        "comparison_complete": comparison_complete,
        "per_entity": per_entity,
        "missing_entities": missing_entities,
        "missing_metrics": missing_metrics,
        "missing": missing,
        "historical_ok": historical_ok,
        "historical_detail": historical_detail,
        "comparison_ok": comparison_ok,
        "comparison_detail": comparison_detail,
        # Partial-result contract (generic, additive fields only).
        "coverage": coverage.to_dict(),
        "validated_entities": list(coverage.validated_entities),
        "validated_metrics": list(coverage.validated_metrics),
        "excluded_entities": list(coverage.excluded_entities),
        "excluded_metrics": list(coverage.excluded_metrics),
        "sufficient_for_partial": bool(coverage.sufficient),
        "partial": bool(coverage.partial),
        "exclusion_note": _exclusion_note,
    }

__all__ = [
    "CAGR_FORMULA",
    "ComparisonEvidence",
    "EvidenceCoverage",
    "EvidenceRequirement",
    "STATS_ASSUMPTIONS",
    "STATS_FORMULA",
    "TypedFigure",
    "_distinct_years",
    "_margin_pair",
    "_parse_date",
    "_span_days",
    "assert_compatible_inputs",
    "build_evidence_coverage",
    "build_trace",
    "check_entity_completeness",
    "check_research_completeness",
    "comparability_unknowns",
    "comparison_confidence",
    "compute_cagr",
    "compute_comparison_stats",
    "compute_net_margins",
    "compute_pct_change",
    "compute_yearly_stats",
    "coverage_exclusion_note",
    "evidence_driven_confidence",
    "format_runtime_trace",
    "insufficient_reason",
    "series_span_days",
    "validate_calculation_inputs",
    "validate_comparison",
    "validate_historical_coverage",
]
