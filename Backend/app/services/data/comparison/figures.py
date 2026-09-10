"""Snippet-figure semantics (entity/metric binding). Split from comparison.py; behavior unchanged."""

from __future__ import annotations

import logging
import re

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .currency import _ISO_CURRENCY_RE

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Figure metric labelling: prevents funding-vs-cost style false comparisons
# (the shared root cause with the tech-startup vs robotics-startup failure).
# ---------------------------------------------------------------------------
# NOTE: "startup_cost" is deliberately FIRST and distinct from generic
# "cost" and "funding": "$40K startup cost" must bind metric=startup_cost
# (never generic money/cost) so funding != startup_cost and the two can
# never enter one comparison. Order matters -- figure_metric_label returns
# the first matching pattern.
_FIGURE_METRIC_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("startup_cost", re.compile(r"\bstartup\s*costs?\b", re.IGNORECASE)),
    ("revenue", re.compile(r"\b(revenue|revenues|sales|turnover)\b", re.IGNORECASE)),
    ("funding", re.compile(r"\b(funding|funded|raised|raising|investment|valuation|financing)\b", re.IGNORECASE)),
    ("deal", re.compile(r"\b(sold\s+for|sold|acqui(red|sition)|deal)\b", re.IGNORECASE)),
    ("cost", re.compile(r"\b(cost|costs|expense|expenses|spend|spending)\b", re.IGNORECASE)),
    ("price", re.compile(r"\b(price|prices|share\s*price|close|closing)\b", re.IGNORECASE)),
    ("profit", re.compile(r"\b(profit\w*|margin|net\s*income|earnings|ebitda)\b", re.IGNORECASE)),
    ("market_cap", re.compile(r"\b(market\s*cap|marketcap)\b", re.IGNORECASE)),
    # "Market size" phrasing ("the market was valued at $X billion",
    # "market size, share & forecast", "industry is projected to reach...")
    # is the single most common metric cue in industry/sector research
    # reports, yet was entirely absent from this list -- every such figure
    # fell through with metric=None ("missing metric (WHAT unknown)" in
    # is_figure_comparison_eligible), so market-size comparisons could
    # never produce a comparison/bar chart even with clean, cited,
    # same-currency numbers on both sides. Ordered after market_cap so a
    # literal "market cap" mention still wins that more specific label.
    (
        "market_size",
        re.compile(
            r"\bmarket\s*(size|sizing|value|valued|worth|share)\b"
            r"|\b(market|industry)\s+(was|is|reached|stood\s+at|"
            r"projected|expected|estimated|forecast(ed)?)\b",
            re.IGNORECASE,
        ),
    ),
    # Count nouns ("16 billion views", "50M subscribers", "12,655 open
    # jobs"): the WHAT for count-unit figures. Ordered LAST so an explicit
    # money cue in the same window ("$50M funding for 1000 users") still
    # wins the more specific financial label.
    ("views", re.compile(r"\bviews?\b", re.IGNORECASE)),
    ("subscribers", re.compile(r"\bsubscribers?\b", re.IGNORECASE)),
    ("followers", re.compile(r"\bfollowers?\b", re.IGNORECASE)),
    ("jobs", re.compile(r"\b(jobs?|job\s*openings?|openings?|vacanc\w+)\b", re.IGNORECASE)),
    ("users", re.compile(r"\busers?\b", re.IGNORECASE)),
    ("downloads", re.compile(r"\b(downloads?|installs?)\b", re.IGNORECASE)),
    ("employees", re.compile(r"\b(employees?|headcount|staff|workers?)\b", re.IGNORECASE)),
    ("customers", re.compile(r"\b(customers?|clients?|members?)\b", re.IGNORECASE)),
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
        if not entity:
            continue
        name = str(entity).lower()
        if name in lowered:
            return str(entity)
    # Second pass, generic-noun-stripped: a broad sector/industry entity
    # ("AI industry", "artificial industry", "the medical market") almost
    # never appears verbatim in a source snippet -- sources name the
    # specific thing ("AI in Healthcare Market", "medical devices market"),
    # not the user's generic phrasing. Stripping trailing generic nouns and
    # retrying lets that core word still bind instead of the entity being
    # dropped as "insufficient validated evidence" on every industry-level
    # comparison. Guarded to >=3 chars so this never falls back to a bare
    # 1-2 letter acronym ("ai", "it") matching as a substring of unrelated
    # words.
    for entity in entities or []:
        if not entity:
            continue
        core = re.sub(
            r"\b(industry|industries|sector|sectors|market|markets|the|global)\b",
            "",
            str(entity).lower(),
        )
        core = " ".join(core.split())
        if len(core) >= 3 and core in lowered:
            return str(entity)
    return None


# ---------------------------------------------------------------------------
# Hardening contracts (Phase H): type-safe figures, calculation guards,
# research completeness, and currency inference. All pure, all reuse the
# existing evidence model -- no new planner, no new engine.
# ---------------------------------------------------------------------------

def _infer_currency_for_symbol(symbol: Optional[str]) -> Optional[str]:
    """Deterministic reporting-currency inference from a Yahoo symbol suffix.

    Exchange suffix decides: .NS/.BO -> INR, .KS/.KQ -> KRW, .T -> JPY,
    .SS/.SZ -> CNY, .L -> GBP, .PA/.AS/.DE etc -> EUR, otherwise USD
    (US-listed common stock / ADR). Returns None for empty input. This is
    an explicitly documented heuristic for evidence objects that would
    otherwise carry currency=None (unknown); callers record it as inferred.
    Pure.
    """
    if not symbol:
        return None
    upper = str(symbol).strip().upper()
    if "." not in upper:
        return "USD"
    suffix = upper.rsplit(".", 1)[-1]
    if suffix in ("NS", "BO"):
        return "INR"
    if suffix in ("KS", "KQ"):
        return "KRW"
    if suffix == "T":
        return "JPY"
    if suffix in ("SS", "SZ", "HK"):
        return "CNY" if suffix in ("SS", "SZ") else "HKD"
    if suffix in ("L", "IL"):
        return "GBP"
    if suffix in ("PA", "AS", "DE", "MI", "MC", "BR"):
        return "EUR"
    if suffix in ("TO", "V", "CN"):
        return "CAD"
    if suffix in ("AX", "SI"):
        return "AUD" if suffix == "AX" else "SGD"
    return "USD"


def is_figure_comparison_eligible(figure: Any) -> Tuple[bool, str]:
    """Strict comparison-eligibility for one figure (dict or TypedFigure).

    A comparison-eligible figure must carry enough semantic metadata to
    safely enter comparison math/tables/bars/winners:

    - entity (WHO) and metric (WHAT) bound -- never generic "money"
    - numeric value (WHAT amount) and unit class (money vs percent vs count)
    - explicit ISO currency when unit is money (USD/JPY/CNY/...; generic
      "currency"/"money" is explicitly unknown and FAILS; counts need none)
    - scale (magnitude multiplier; 1 when already in single units)
    - definition (what the number MEANS; defaults to metric when absent
      only if metric itself is a specific cue like funding/startup_cost)
    - source_type (snippet/yahoo/...) provenance marker

    Period/frequency/source_url/historical-status are recorded when
    available and REQUIRED for historical comparison math (callers check
    them via validate_comparison), but a snapshot figure with full
    entity/metric/currency binding may still be eligible for snapshot
    prose tables -- hence they gate the calculation path, not this flag.

    Raw figures are NEVER discarded here: False means "prose/context only",
    never "drop". Pure.
    """
    get = (
        (lambda key, default=None: figure.get(key, default))
        if isinstance(figure, dict)
        else (lambda key, default=None: getattr(figure, key, default))
    )
    entity = get("entity")
    metric = get("metric")
    value = get("value")
    unit = get("unit")
    currency = get("currency")
    scale = get("scale")
    definition = get("definition")
    source_type = get("source_type", "snippet")

    if not entity or not str(entity).strip():
        return False, "missing entity (WHO unknown)"
    if not metric or not str(metric).strip():
        return False, "missing metric (WHAT unknown)"
    if str(metric).strip().lower() in ("money", "currency", "value", "amount", "unknown", ""):
        return False, f"generic metric {metric!r} cannot compare"
    try:
        value_f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False, "missing/invalid numeric value"
    import math as _math

    if not _math.isfinite(value_f):
        return False, "non-finite numeric value"
    if str(unit or "").strip().lower() not in ("money", "percent", "count"):
        return False, f"missing/unknown unit {unit!r}"
    if str(unit or "").strip().lower() == "money":
        code = str(currency or "").strip().upper()
        if not _ISO_CURRENCY_RE.match(code or ""):
            return False, f"money figure lacks explicit ISO currency (got {currency!r})"
    # Scale: None means "unstated" -> treat as 1 only when the value was
    # already extracted scaled (pipeline multiplies k/M/B at extraction).
    # An explicit scale is preferred but its absence must not silently
    # re-scale; require presence OR a money/percent unit with finite value.
    # (Lenient here by design: scale mismatch is normalized, not blocked.)
    if scale is not None:
        try:
            scale_f = float(scale)  # type: ignore[arg-type]
            if not _math.isfinite(scale_f) or scale_f <= 0:
                return False, f"invalid scale {scale!r}"
        except (TypeError, ValueError):
            return False, f"invalid scale {scale!r}"
    # Definition: must be specific. A bare metric IS the definition when
    # the metric cue itself is specific (funding/startup_cost/revenue/...).
    effective_definition = str(definition or metric or "").strip().lower()
    if not effective_definition or effective_definition in (
        "money", "currency", "value", "amount", "unknown",
    ):
        return False, "missing definition (WHAT-measured-as unknown)"
    if not source_type or not str(source_type).strip():
        return False, "missing source_type/provenance"
    return True, "comparison-eligible: entity+metric+value+unit+currency+definition bound"

__all__ = [
    "_FIGURE_METRIC_PATTERNS",
    "_infer_currency_for_symbol",
    "figure_entity_label",
    "figure_metric_label",
    "figures_share_metric",
    "is_figure_comparison_eligible",
]
