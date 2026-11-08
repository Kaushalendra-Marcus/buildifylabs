"""Deterministic sanity checks on an executed query's result.

The SQL sanitizer proves a query is *safe*; nothing proves it is *right*. These
cheap checks catch the results that most often mean the generated query did not
match the data (a filter that matches nothing, a column that is entirely empty),
so the answer can say so instead of narrating a confident non-answer.

Each note is ``{"code": str, "message": str}``. Pure; never raises.
"""

import re
from typing import Any, Dict, List, Optional, Sequence

_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)

ALL_EMPTY = "all_values_empty"
EMPTY_BUT_DATA = "empty_result_nonempty_table"
HIT_LIMIT = "result_hit_row_limit"


def compact_profile(profile: Optional[dict]) -> Dict[str, Any]:
    """Small, JSON-safe summary of a data profile for research_state."""
    try:
        if not isinstance(profile, dict) or not profile:
            return {}
        out: Dict[str, Any] = {}
        for key in ("row_count", "date_column", "date_min", "date_max"):
            if profile.get(key) is not None:
                out[key] = profile[key]
        sparse = {
            col["name"]: col["nulls"]
            for col in (profile.get("columns") or [])
            if isinstance(col, dict) and (col.get("nulls") or 0) > 0
        }
        if sparse:
            out["columns_with_empty_values"] = dict(list(sparse.items())[:10])
        return out
    except Exception:
        return {}


def sql_result_sanity(
    rows: Optional[Sequence[dict]],
    sql: Optional[str],
    profile: Optional[dict] = None,
) -> List[Dict[str, str]]:
    """Notes about a result that looks wrong or incomplete (empty list = fine)."""
    notes: List[Dict[str, str]] = []
    try:
        rows = list(rows or [])
        total = (profile or {}).get("row_count") if isinstance(profile, dict) else None
        if not rows:
            if isinstance(total, int) and total > 0 and sql:
                notes.append({
                    "code": EMPTY_BUT_DATA,
                    "message": (
                        f"The query returned no rows although your data has {total:,} "
                        "rows - a filter may not match how the values are written."
                    ),
                })
            return notes
        values = [v for row in rows if isinstance(row, dict) for v in row.values()]
        if values and all(v is None for v in values):
            notes.append({
                "code": ALL_EMPTY,
                "message": "Every value in the result is empty, so there is nothing to conclude from it.",
            })
        match = _LIMIT_RE.search(sql or "")
        if match and int(match.group(1)) == len(rows):
            notes.append({
                "code": HIT_LIMIT,
                "message": (
                    f"The result reached its {len(rows)}-row limit, so totals "
                    "computed from these rows may be partial."
                ),
            })
    except Exception:
        return notes
    return notes


def should_cap_confidence(notes: Sequence[Dict[str, str]]) -> bool:
    """True when a note means the answer cannot be trusted at full confidence."""
    return any(note.get("code") == ALL_EMPTY for note in notes or [])


__all__ = [
    "ALL_EMPTY",
    "EMPTY_BUT_DATA",
    "HIT_LIMIT",
    "compact_profile",
    "should_cap_confidence",
    "sql_result_sanity",
]
