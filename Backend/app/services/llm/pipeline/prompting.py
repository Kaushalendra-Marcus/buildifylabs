"""Prompt assembly, citations, clarification plumbing. Split from langchain_pipeline.py; behavior unchanged."""

import difflib
import json
import logging
import re

from typing import Any, Dict, Optional, Sequence

from .models import Decision, PipelineOutput, SOURCE_SCOPES
from .prompts import PREFERRED_VISUAL_PATTERNS, PROMPT_MAX_ROWS

logger = logging.getLogger(__name__)



def _normalize_text(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def detect_preferred_visual(query: str) -> Optional[str]:
    """Detect an explicitly requested output shape from the query text."""
    lowered = query.lower()
    for pattern, kind in PREFERRED_VISUAL_PATTERNS:
        if re.search(pattern, lowered):
            return kind
    return None


def sanitize_citations(answer: str, source_count: int) -> str:
    """Drop citation markers that point at no listed source ([0], [99] when
    only 3 snippets exist). Valid markers pass through untouched. Year
    brackets ([2024]) are never citations and are always preserved."""
    try:
        from app.services.data.canonical import sanitize_citations_safe

        return sanitize_citations_safe(answer, source_count)
    except Exception:
        pass
    if source_count <= 0:
        # Preserve 4-digit years even with zero sources.
        return re.sub(r"\[(?!(?:19|20)\d{2}\])\d+\]", "", answer)

    def _keep(match: "re.Match[str]") -> str:
        raw = match.group(1)
        if len(raw) >= 4:
            return match.group(0)
        number = int(raw)
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
    macro_note: Optional[str] = None,
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
    macro_note = macro_note or ""
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
        # Fail-closed no-evidence mode (P1#26): never instruct the model to
        # use its own knowledge for statistics. Without web evidence the
        # honest response is an insufficiency report, not hallucinated data.
        news_section = (
            "No usable live-web evidence was retrieved for this query. "
            "Do NOT use your own knowledge for figures, statistics, or "
            "comparisons. State clearly that evidence is insufficient, "
            "describe what is missing, set confidence 0.0, and return NO "
            "numeric graph/comparison visual."
        )

    company_section = company_name or "Not provided"

    # Separate channels (P0#15): market_data and macro_data are NEVER merged.
    # A market chart must never contain CPI/GDP/unemployment series.
    market_section = (
        "\n".join(
            f"- {item.get('entity', 'series')}: "
            f"{len(item.get('values', []) or [])} points "
            f"({', '.join(str(label) for label in (item.get('labels', []) or [])[:3])}...)"
            for item in list(market_data)[:6]
        )
        if market_data
        else "none"
    )
    macro_section = (
        "\n".join(
            f"- {item.get('entity', 'series')}: "
            f"{len(item.get('values', []) or [])} points "
            f"({', '.join(str(label) for label in (item.get('labels', []) or [])[:3])}...)"
            for item in list(macro_data)[:6]
        )
        if macro_data
        else "none"
    )
    if macro_note:
        # Honest-gap disclosure (P4): the narrator must state the
        # structural coverage gap plainly instead of implying no evidence
        # exists anywhere.
        macro_section += (
            "\nData-gap notice (state this honestly in the answer; it is "
            f"status, not evidence): {macro_note}"
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
    validated_section = "None -- no deterministically validated comparison evidence."
    if comparison_gate.get("applies"):
        if comparison_gate.get("blocked"):
            gate_section = (
                "HISTORICAL COMPARISON GATE: BLOCKED -- evidence insufficient. "
                f"{comparison_gate.get('blocked_reason', '')} "
                "Do NOT present a comparison chart or winners. State what data "
                "is missing, keep confidence at 0, and do not invent figures."
            )
            validated_section = (
                "None -- the comparison gate BLOCKED, so NOTHING below counts "
                "as validated comparison evidence. Every number in Web Search "
                "Results / Fundamentals / Market Series is UNVALIDATED CONTEXT."
            )
        else:
            gate_section = (
                "HISTORICAL COMPARISON GATE: PASSED -- validated multi-year "
                f"evidence for {comparison_gate.get('entities', [])} over "
                f"{comparison_gate.get('years')}Y. Quote the Computed "
                "Statistics comparison_stats exactly (winners + pct changes + "
                "formula/assumptions) and cite sources for explanatory claims."
            )
            validated_section = (
                "The Computed Statistics comparison_stats block below. ONLY "
                "this block may support numerical claims, winners, and "
                "charts. Everything else (snippets, snapshots, series) is "
                "UNVALIDATED CONTEXT: usable for hedged qualitative reasons "
                "with [n] citations, never for quantitative comparisons."
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

VALIDATED EVIDENCE (only this may support numerical claims, winners, charts):
{validated_section}

Comparison Gate (must obey: BLOCKED means no chart, no winners, state missing data):
{gate_section}

Market Series (SHORT-TERM one-month values only - NEVER use for "last N years"):
{market_section}

Macro Series (economy-wide FRED indicators - NEVER chart as company market data):
{macro_section}

Price/Financial History (multi-year validated evidence - use for historical asks):
{history_section}

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
    """Robust JSON extraction: whole/fenced parse, balanced-brace scan with
    string awareness (prose braces never confuse it), legacy fallback."""
    try:
        from app.services.data.canonical import extract_json_robust

        return extract_json_robust(text)
    except ValueError:
        raise
    except Exception:
        pass
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
        "research_state": None,
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

__all__ = [
    "_normalize_text",
    "_same_question",
    "_truncate_rows",
    "build_prompt",
    "detect_preferred_visual",
    "extract_json",
    "fallback_output",
    "normalize_pipeline_payload",
    "sanitize_citations",
]
