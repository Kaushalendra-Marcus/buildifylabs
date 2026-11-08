"""Narration-time aids that reduce what the model has to type or invent.

1. **Number placeholders** (opt-in, ``ENABLE_NUMBER_PLACEHOLDERS``). The narrator
   is shown the code-computed values as ``key = value`` and told to write
   ``{{key}}`` (optionally ``{{key|pct}}``, ``|money``, ``|compact``, ``|int``)
   instead of typing the figure. The server substitutes the exact value, so a
   number the model cannot type is a number it cannot hallucinate. A placeholder
   that resolves to nothing removes its sentence rather than shipping ``{{...}}``.
2. **Driver rule.** When the pipeline computed ``drivers`` (a deterministic
   decomposition of a change into per-segment contributions), root causes must
   come from it.
3. **Data coverage.** The row count / date range of the user's data is handed to
   the narrator so it can state the limits of its evidence.

Pure helpers + one prompt-builder; nothing here raises into the pipeline.
"""

import logging
import re
from decimal import Decimal
from numbers import Number
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from app.config import get_settings

from . import guard_metrics
from .prose_guard import _rejoin, _split_sentences

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*([A-Za-z0-9_.\[\]]+)\s*(?:\|\s*([a-z_]+)\s*)?\}\}"
)
_MAX_PLACEHOLDER_KEYS = 60
_MAX_ROWS_FOR_KEYS = 5

DRIVER_RULE = (
    "DRIVER RULE: Computed Statistics contains `drivers` - a code-computed "
    "decomposition of the change into per-segment contributions. root_causes MUST "
    "come only from drivers.top_contributors (name the segment and quote its "
    "change), phrased with hedged language. Do not propose causes that are not "
    "in drivers or other supplied evidence; if drivers is inconclusive, say so."
)


# ---------------------------------------------------------------------------
# Flatten + format
# ---------------------------------------------------------------------------

def _safe_key(name: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name)).strip("_") or "x"


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (Number, Decimal)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def flatten_numeric(
    computed_numbers: Optional[dict],
    rows: Optional[Sequence[dict]] = None,
    max_items: int = _MAX_PLACEHOLDER_KEYS,
) -> Dict[str, float]:
    """Dotted-path -> number for every numeric leaf of the computed stats and
    the first few result rows. Keys use only ``[A-Za-z0-9_]`` and ``[i]``."""
    out: Dict[str, float] = {}

    def walk(node: Any, path: str, depth: int) -> None:
        if len(out) >= max_items or depth > 5:
            return
        number = _as_float(node)
        if number is not None:
            out[path] = number
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{_safe_key(key)}", depth + 1)
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(list(node)[:6]):
                walk(value, f"{path}[{index}]", depth + 1)

    try:
        walk(computed_numbers or {}, "computed", 0)
        row_list = list(rows or [])
        if row_list and len(out) < max_items:
            out["row_count"] = float(len(row_list))
        for index, row in enumerate(row_list[:_MAX_ROWS_FOR_KEYS]):
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if len(out) >= max_items:
                    break
                number = _as_float(value)
                if number is not None:
                    out[f"rows[{index}].{_safe_key(key)}"] = number
    except Exception as exc:
        logger.warning("Placeholder flatten failed: %s", exc)
    return out


def format_value(value: float, hint: Optional[str] = None) -> str:
    number = float(value)
    hint = (hint or "").lower()
    if hint == "pct":
        return f"{number:,.1f}%"
    if hint == "money":
        return f"${number:,.0f}" if abs(number) >= 100 else f"${number:,.2f}"
    if hint == "int":
        return f"{number:,.0f}"
    if hint == "compact":
        magnitude = abs(number)
        for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if magnitude >= threshold:
                return f"{number / threshold:,.1f}{suffix}"
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"


def render_placeholders(text: str, values: Dict[str, float]) -> Tuple[str, List[str]]:
    """Substitute known placeholders; unknown ones are left in place and listed."""
    unresolved: List[str] = []

    def _sub(match: "re.Match[str]") -> str:
        key, hint = match.group(1), match.group(2)
        if key in values:
            return format_value(values[key], hint)
        unresolved.append(key)
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text or ""), unresolved


def _fix_text(text: str, values: Dict[str, float]) -> str:
    rendered, unresolved = render_placeholders(text, values)
    if "{{" not in rendered:
        return rendered
    guard_metrics.record_guard("placeholder_unresolved", ", ".join(unresolved[:3]))
    sentences, seps = _split_sentences(rendered)
    drops = {i for i, s in enumerate(sentences) if "{{" in s}
    return _rejoin(sentences, seps, drops)


def finalize_placeholders(output: Any, values: Optional[Dict[str, float]]) -> Any:
    """Fill placeholders in every prose field of a PipelineOutput (in place)."""
    if not values or output is None:
        return output
    try:
        if isinstance(getattr(output, "answer", None), str):
            output.answer = _fix_text(output.answer, values)
        if isinstance(getattr(output, "summary", None), str):
            output.summary = _fix_text(output.summary, values)
        for field_name in ("insights", "root_causes", "recommendations", "followups"):
            items = getattr(output, field_name, None)
            if not isinstance(items, list):
                continue
            fixed: List[Any] = []
            for item in items:
                if isinstance(item, str):
                    text = _fix_text(item, values)
                    if text.strip():
                        fixed.append(text)
                else:
                    fixed.append(item)
            setattr(output, field_name, fixed)
    except Exception as exc:
        logger.warning("Placeholder finalize failed: %s", exc)
        guard_metrics.record_guard("guard_error:placeholders", str(exc))
    return output


class _StreamRenderer:
    """Renders placeholders in streamed text deltas.

    Holds back an unfinished ``{{...`` (or a trailing lone ``{``) until it
    closes, so the user never sees a raw placeholder flash by mid-stream.
    """

    def __init__(self, values: Dict[str, float]) -> None:
        self.values = values
        self.buffer = ""

    def feed(self, chunk: str) -> str:
        self.buffer += chunk or ""
        cut = self.buffer.rfind("{{")
        if cut != -1 and "}}" not in self.buffer[cut:]:
            ready, self.buffer = self.buffer[:cut], self.buffer[cut:]
        elif self.buffer.endswith("{"):
            ready, self.buffer = self.buffer[:-1], "{"
        else:
            ready, self.buffer = self.buffer, ""
        return render_placeholders(ready, self.values)[0]


def stream_wrapper(
    on_token: Callable[[str], Awaitable[None]], values: Dict[str, float]
) -> Callable[[str], Awaitable[None]]:
    renderer = _StreamRenderer(values)

    async def _wrapped(text: str) -> None:
        out = renderer.feed(text)
        if out:
            await on_token(out)

    return _wrapped


# ---------------------------------------------------------------------------
# Prompt addenda
# ---------------------------------------------------------------------------

def _placeholder_instructions(values: Dict[str, float]) -> str:
    lines = [f"- {key} = {format_value(value)}" for key, value in list(values.items())[:_MAX_PLACEHOLDER_KEYS]]
    return (
        "NUMBER PLACEHOLDERS: do not type figures from the list below yourself. "
        "Write them as {{key}} (or {{key|pct}}, {{key|money}}, {{key|compact}}, "
        "{{key|int}}) and the server will insert the exact value. Only use keys "
        "from this list; if you need a figure that is not listed, describe it "
        "qualitatively instead of typing a number.\n" + "\n".join(lines)
    )


def coverage_note(profile: Optional[dict]) -> str:
    """One-line description of what the user's data covers (empty if unknown)."""
    try:
        if not isinstance(profile, dict) or not profile:
            return ""
        bits: List[str] = []
        total = profile.get("row_count")
        if isinstance(total, int):
            bits.append(f"{total:,} rows")
        date_col, lo, hi = (
            profile.get("date_column"), profile.get("date_min"), profile.get("date_max"),
        )
        if date_col and lo and hi:
            bits.append(f"{date_col} spans {lo} to {hi}")
        sparse = [
            f"{col['name']} ({col['nulls']} empty)"
            for col in (profile.get("columns") or [])
            if isinstance(col, dict) and (col.get("nulls") or 0) > 0
        ][:4]
        if sparse:
            bits.append("empty values in " + ", ".join(sparse))
        return "; ".join(bits)
    except Exception:
        return ""


def build_narration_addenda(
    computed_numbers: Optional[dict], db_data: Optional[Sequence[dict]]
) -> Tuple[str, Dict[str, float]]:
    """Extra prompt text for the narration call + the placeholder value map."""
    parts: List[str] = []
    values: Dict[str, float] = {}
    try:
        settings = get_settings()
        if getattr(settings, "ENABLE_NUMBER_PLACEHOLDERS", False):
            values = flatten_numeric(computed_numbers, db_data)
            if values:
                parts.append(_placeholder_instructions(values))
        drivers = (computed_numbers or {}).get("drivers")
        if isinstance(drivers, dict) and drivers:
            parts.append(DRIVER_RULE)
        note = coverage_note(guard_metrics.get_data_profile())
        if note:
            parts.append(
                "DATA COVERAGE (mention it when the time range or sample size "
                f"limits what you can conclude): {note}."
            )
    except Exception as exc:
        logger.warning("Narration addenda failed: %s", exc)
        return "", {}
    return ("\n\n" + "\n\n".join(parts)) if parts else "", values


__all__ = [
    "DRIVER_RULE",
    "build_narration_addenda",
    "coverage_note",
    "finalize_placeholders",
    "flatten_numeric",
    "format_value",
    "render_placeholders",
    "stream_wrapper",
]
