"""Deterministic extraction of box-office evidence from retrieved web text.

This is intentionally conservative: only explicit ``day N`` collection
claims are promoted to a series. A headline total or an unrelated percentage
remains ordinary cited text and cannot become a daily chart.
"""

import re
from typing import Any


_MONEY = r"(?:₹|Rs\.?|INR|\$|USD|£|€)\s*[\d,]+(?:\.\d+)?\s*(?:crore|crores|cr|million|billion|mn|bn|m|b)?"
_DAY_RE = re.compile(
    rf"(?:\bday\s*(?P<day_before>\d{{1,3}})\b[^.!?\n]{{0,45}}?(?P<money_after>{_MONEY})"
    rf"|(?P<money_before>{_MONEY})[^.!?\n]{{0,25}}?\bon\s+\bday\s*(?P<day_after>\d{{1,3}})\b)",
    re.IGNORECASE,
)
_BUDGET_RE = re.compile(
    rf"\b(?:budget|production budget|made on a budget of)\b[^.!?\n]{{0,80}}?({_MONEY})",
    re.IGNORECASE,
)
_RELEASE_RE = re.compile(
    r"\b(?:released?|release date|released on)\b[^.!?\n]{0,40}?"
    r"(\b\d{1,2}\s+[A-Z][a-z]+\s+20\d{2}\b|\b[A-Z][a-z]+\s+\d{1,2},\s+20\d{2}\b|\b20\d{2}-\d{2}-\d{2}\b)",
    re.IGNORECASE,
)


def _money_value(raw: str) -> float | None:
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*([a-z]+)?", raw or "", re.I)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    scale = {"crore": 1e7, "crores": 1e7, "cr": 1e7, "million": 1e6,
             "billion": 1e9, "mn": 1e6, "bn": 1e9, "m": 1e6, "b": 1e9}
    return value * scale.get((match.group(2) or "").lower(), 1)


def extract_box_office_history(snippets: list[str], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Return explicit daily collections and an optional budget.

    Values are stored in rupees while ``display_values`` preserves the source
    wording for the table. Source indices are 1-based citation indices.
    """
    points: dict[int, dict[str, Any]] = {}
    budget: dict[str, Any] | None = None
    release_date: str | None = None
    for source_index, text in enumerate(snippets or [], start=1):
        for match in _DAY_RE.finditer(str(text)):
            day_raw = match.group("day_before") or match.group("day_after")
            money_raw = match.group("money_after") or match.group("money_before")
            value = _money_value(money_raw)
            if value is None:
                continue
            day = int(day_raw)
            points.setdefault(day, {
                "day": day,
                "label": f"Day {day}",
                "value": value,
                "display_value": money_raw.strip(),
                "source_id": f"web:{source_index - 1}",
                "citation": source_index,
            })
        budget_match = _BUDGET_RE.search(str(text))
        if budget is None and budget_match:
            value = _money_value(budget_match.group(1))
            if value is not None:
                budget = {
                    "value": value,
                    "display_value": budget_match.group(1).strip(),
                    "source_id": f"web:{source_index - 1}",
                    "citation": source_index,
                }
        release_match = _RELEASE_RE.search(str(text))
        if release_date is None and release_match:
            release_date = release_match.group(1).strip()
    return {
        "points": [points[key] for key in sorted(points)],
        "budget": budget,
        "release_date": release_date,
    }
