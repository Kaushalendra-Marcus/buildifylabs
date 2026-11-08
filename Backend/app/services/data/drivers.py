"""Deterministic driver (root-cause) decomposition for "why did X change?".

Instead of letting the model imagine causes, code compares the latest window of
the user's data with the window before it and attributes the change to segments:

    change = recent_total - prior_total
    contribution(segment) = recent(segment) - prior(segment)

The result is stored in ``computed["drivers"]``; the narration prompt then
requires root causes to come only from ``drivers.top_contributors`` (see
narration_aids.DRIVER_RULE) and every number in it is evidence for the
number guard.

Windows are anchored on the *data's* latest date (not today). Everything is a
read-only aggregate over the caller's own per-user table, built from the data
profile (column names come from information_schema, quoted). Fail-soft: None.
"""

import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.services.data.executor import _quote_ident

logger = logging.getLogger(__name__)

DRIVER_QUERY_RE = re.compile(
    r"\b(why|reason|cause[sd]?|causing|driver|drivers|driving|what\s+changed|"
    r"behind|contribut\w*|explain)\b|"
    r"\b(drop(?:ped|ping)?|declin\w+|fell|fall(?:ing)?|decreas\w+|increas\w+|"
    r"spik\w+|surg\w+|jump\w*|grew|growth)\b",
    re.IGNORECASE,
)
_VALUE_NAME_RE = re.compile(
    r"revenue|sales|amount|total|profit|income|cost|price|value|spend|units|qty|quantity",
    re.IGNORECASE,
)
_DIM_NAME_RE = re.compile(
    r"region|product|category|channel|segment|customer|city|state|type|status|country|plan",
    re.IGNORECASE,
)
_MIN_SPAN_DAYS = 13
_MAX_WINDOW_DAYS = 90
_MAX_DIMENSIONS = 3
_MAX_CONTRIBUTORS = 6


def is_driver_query(query: str) -> bool:
    try:
        return bool(DRIVER_QUERY_RE.search(query or ""))
    except Exception:
        return False


def _parse_iso(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _is_id_like(name: str) -> bool:
    lowered = str(name).lower()
    return lowered == "id" or lowered.endswith("_id") or lowered.endswith("id") and len(lowered) <= 4


def _pick_value_column(columns: List[dict]) -> Optional[dict]:
    numeric = [c for c in columns if c.get("type") == "numeric" and not _is_id_like(c["name"])]
    named = [c for c in numeric if _VALUE_NAME_RE.search(c["name"])]
    return (named or numeric or [None])[0]


def _pick_dimensions(columns: List[dict]) -> List[dict]:
    candidates = [
        c for c in columns
        if c.get("type") == "text"
        and not c.get("iso_text")
        and not _is_id_like(c["name"])
        and 2 <= (c.get("distinct") or 0) <= 20
    ]
    candidates.sort(key=lambda c: 0 if _DIM_NAME_RE.search(c["name"]) else 1)
    return candidates[:_MAX_DIMENSIONS]


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


async def compute_drivers(db, table_name: str, profile: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Compare the latest window with the previous one and attribute the change.

    Returns None when the table has no usable date axis, no numeric measure, too
    little history, or a query fails.
    """
    try:
        if not isinstance(profile, dict):
            return None
        columns = [c for c in (profile.get("columns") or []) if isinstance(c, dict)]
        date_col = next((c for c in columns if c.get("name") == profile.get("date_column")), None)
        low, high = _parse_iso(profile.get("date_min")), _parse_iso(profile.get("date_max"))
        if date_col is None or low is None or high is None:
            return None
        span = (high - low).days
        if span < _MIN_SPAN_DAYS:
            return None
        value_col = _pick_value_column(columns)
        if value_col is None:
            return None
        dimensions = _pick_dimensions(columns)

        window = max(7, min(span // 2, _MAX_WINDOW_DAYS))
        end_exclusive = high + timedelta(days=1)
        recent_start = end_exclusive - timedelta(days=window)
        prior_start = recent_start - timedelta(days=window)
        params = {
            "ps": prior_start.isoformat(),
            "rs": recent_start.isoformat(),
            "re": end_exclusive.isoformat(),
        }

        dialect = getattr(getattr(getattr(db, "bind", None), "dialect", None), "name", "")
        raw_type = str(date_col.get("raw_type") or "").lower()

        def bound(name: str) -> str:
            if dialect == "postgresql" and "timestamp" in raw_type:
                return f"CAST(:{name} AS TIMESTAMP)"
            if dialect == "postgresql" and raw_type == "date":
                return f"CAST(:{name} AS DATE)"
            return f":{name}"

        d, v = _quote_ident(date_col["name"]), _quote_ident(value_col["name"])
        recent_sum = f"SUM(CASE WHEN {d} >= {bound('rs')} AND {d} < {bound('re')} THEN {v} ELSE 0 END)"
        prior_sum = f"SUM(CASE WHEN {d} >= {bound('ps')} AND {d} < {bound('rs')} THEN {v} ELSE 0 END)"
        table = _quote_ident(table_name)

        overall = (await db.execute(
            text(f"SELECT {recent_sum} AS recent, {prior_sum} AS prior FROM {table}"), params
        )).mappings().first()
        recent_total, prior_total = _as_float(overall["recent"]), _as_float(overall["prior"])
        if recent_total == 0 and prior_total == 0:
            return None
        change = recent_total - prior_total

        contributors: List[Dict[str, Any]] = []
        for dim in dimensions:
            q = _quote_ident(dim["name"])
            result = await db.execute(
                text(
                    f"SELECT {q} AS k, {recent_sum} AS recent, {prior_sum} AS prior "
                    f"FROM {table} WHERE {q} IS NOT NULL GROUP BY {q}"
                ),
                params,
            )
            for row in result.mappings():
                recent, prior = _as_float(row["recent"]), _as_float(row["prior"])
                delta = recent - prior
                contributors.append({
                    "dimension": dim["name"],
                    "value": str(row["k"]),
                    "recent": round(recent, 2),
                    "prior": round(prior, 2),
                    "change": round(delta, 2),
                    "share_of_change_pct": round(delta / change * 100.0, 1) if change else None,
                })
        contributors.sort(key=lambda c: abs(c["change"]), reverse=True)

        return {
            "metric": value_col["name"],
            "date_column": date_col["name"],
            "recent_period": f"{recent_start.isoformat()} to {high.isoformat()}",
            "prior_period": f"{prior_start.isoformat()} to {(recent_start - timedelta(days=1)).isoformat()}",
            "recent_total": round(recent_total, 2),
            "prior_total": round(prior_total, 2),
            "change": round(change, 2),
            "pct_change": round(change / prior_total * 100.0, 2) if prior_total else None,
            "top_contributors": contributors[:_MAX_CONTRIBUTORS],
            "method": (
                f"Compared the latest {window} days of data with the {window} days before "
                f"it, split by {', '.join(d_['name'] for d_ in dimensions) or 'no segment column'}. "
                "A segment's contribution is its change in total; share_of_change_pct is "
                "that change as a percentage of the overall change."
            ),
        }
    except Exception as exc:
        logger.warning("Driver analysis skipped: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass
        return None


__all__ = ["DRIVER_QUERY_RE", "compute_drivers", "is_driver_query"]
