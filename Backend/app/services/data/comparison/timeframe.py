"""Periods, metrics, comparison intent. Split from comparison.py; behavior unchanged."""

from __future__ import annotations

import logging
import re

from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from .symbols import METRIC_PROFIT, METRIC_REVENUE, METRIC_STOCK, _PROFIT_RE, _REVENUE_RE, _STOCK_RE

logger = logging.getLogger(__name__)


# "last 3 years" / "past three years" / "3-year" / "over 3 years" ...
# Word forms cover one through ten so "last eight/nine years" parses.
_PERIOD_RE = re.compile(
    r"\b(?:last|past|previous|over(?:\s+the)?|trailing)\s+"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]?\s*"
    r"(years?|yrs?|y)\b"
    r"|\b(\d+)\s*[- ]?years?\s*(comparison|history|trend|performance)?\b"
    r"|\b(\d+)\s*y\b",
    re.IGNORECASE,
)
# Explicit calendar ranges: "2022 to 2025", "2022-2025", "from 2022 to 2025",
# "between 2022 and 2025". Relative forms stay in _PERIOD_RE above.
_EXPLICIT_RANGE_RE = re.compile(
    r"\b(?:from\s+)?(19\d{2}|20\d{2})\s*(?:to|through|thru|until|\-|\u2013|\u2014)\s*(19\d{2}|20\d{2})\b"
    r"|\bbetween\s+(19\d{2}|20\d{2})\s+and\s+(19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
# A bare fiscal-year anchor ("FY2023", "fiscal 2023", "fiscal year 2024").
_FISCAL_YEAR_RE = re.compile(
    r"\bF\.?\s*Y\.?\s*(20\d{2})\b|\bfiscal(?:\s+year)?\s+(20\d{2})\b",
    re.IGNORECASE,
)
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_COMPARISON_RE = re.compile(
    r"\b(vs\.?|versus|compare|comparison|contrast|between|"
    r"which\s+(company|one)|stronger|better|best)\b",
    re.IGNORECASE,
)


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


def detect_generic_metric_phrases(query: str) -> List[str]:
    """Raw metric-like phrases for arbitrary metrics (generic fallback).

    Captures the span after "on"/"in terms of"/"by"/"for" up to the next
    scope cue, split on commas/and. Used only to decide whether the user
    already supplied a metric (clarification discipline), never for Yahoo
    routing. Pure.
    """
    text = query or ""
    match = re.search(
        r"\b(?:on|in\s+terms\s+of|by|for|in|regarding|about)\s+(.+)",
        text, re.IGNORECASE,
    )
    if not match:
        return []
    segment = match.group(1)
    cut = re.search(
        r"\b(over|during|from|between|in\s+\d{4}|last|past|trailing|show|which|explain|with|and\s+explain)\b",
        segment, re.IGNORECASE,
    )
    if cut:
        segment = segment[:cut.start()]
    parts = re.split(r"\s*,\s*|\s+and\s+", segment)
    out: List[str] = []
    for part in parts:
        phrase = " ".join(part.strip().split())[:80]
        if phrase and len(phrase) >= 3:
            out.append(phrase)
    return out[:4]


def detect_period_years(query: str) -> Optional[int]:
    """Detect an explicit historical window in years ("last 3 years" -> 3)."""
    parsed = parse_time_range(query or "")
    return parsed.years


@dataclass
class TimeRange:
    """Normalized time-range representation (structural, never a loose string).

    kind is one of "relative_years" ("last 3 years"), "calendar_year_range"
    ("2022 to 2025" / "FY2023"), or "none" (no explicit window detected).
    """

    kind: str = "none"
    years: Optional[int] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "years": self.years,
            "start_year": self.start_year,
            "end_year": self.end_year,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "TimeRange":
        if not isinstance(payload, dict):
            return cls()
        kind = str(payload.get("kind") or "none")
        if kind not in ("relative_years", "calendar_year_range", "none"):
            kind = "none"
        try:
            years = int(payload["years"]) if payload.get("years") is not None else None
        except (TypeError, ValueError):
            years = None
        try:
            start = int(payload["start_year"]) if payload.get("start_year") is not None else None
        except (TypeError, ValueError):
            start = None
        try:
            end = int(payload["end_year"]) if payload.get("end_year") is not None else None
        except (TypeError, ValueError):
            end = None
        return cls(
            kind=kind, years=years, start_year=start, end_year=end,
            label=payload.get("label"),
        )


def parse_time_range(query: str) -> TimeRange:
    """Parse a query's time window into a structural TimeRange.

    Supports relative windows ("last N years", "past N years", "N-year",
    "trailing N years", "3Y") and explicit ranges ("2022 to 2025",
    "2022-2025", "from 2022 to 2025", "between 2022 and 2025", "FY2023" /
    "fiscal 2023"). Explicit ranges win over relative ones when both appear.
    Pure and deterministic -- no LLM, no network.
    """
    text = query or ""
    explicit = _EXPLICIT_RANGE_RE.search(text)
    if explicit:
        groups = [g for g in explicit.groups() if g]
        try:
            start_year, end_year = int(groups[0]), int(groups[1])
        except (IndexError, TypeError, ValueError):
            start_year = end_year = None
        if start_year and end_year:
            if start_year > end_year:
                start_year, end_year = end_year, start_year
            span = end_year - start_year + 1
            return TimeRange(
                kind="calendar_year_range",
                years=span,
                start_year=start_year,
                end_year=end_year,
                label=f"{start_year}-{end_year}",
            )
    match = _PERIOD_RE.search(text)
    if match:
        for group in (match.group(1), match.group(3)):
            if group:
                raw = group.strip().lower()
                years: Optional[int] = None
                if raw.isdigit():
                    years = int(raw)
                elif raw in _WORD_NUM:
                    years = int(_WORD_NUM[raw])
                if years:
                    return TimeRange(
                        kind="relative_years", years=years, label=f"{years}Y"
                    )
        # NOTE: the short "3y/5y/10y" form is NOT match.group(4) (that slot
        # is the optional comparison-word group) -- match it directly so the
        # form actually works (previously dead).
        short_match = re.search(r"\b(\d+)\s*y\b", text, re.IGNORECASE)
        if short_match:
            years = int(short_match.group(1))
            return TimeRange(kind="relative_years", years=years, label=f"{years}Y")
    fiscal = _FISCAL_YEAR_RE.search(text)
    if fiscal:
        year_text = fiscal.group(1) or fiscal.group(2)
        try:
            year = int(year_text)
        except (TypeError, ValueError):
            year = None
        if year:
            return TimeRange(
                kind="calendar_year_range", years=1,
                start_year=year, end_year=year, label=f"FY{year}",
            )
    return TimeRange()


def is_comparison_query(query: str) -> bool:
    return bool(_COMPARISON_RE.search(query or ""))

__all__ = [
    "TimeRange",
    "_COMPARISON_RE",
    "_EXPLICIT_RANGE_RE",
    "_FISCAL_YEAR_RE",
    "_PERIOD_RE",
    "_WORD_NUM",
    "detect_generic_metric_phrases",
    "detect_metrics",
    "detect_period_years",
    "is_comparison_query",
    "parse_time_range",
]
