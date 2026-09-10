"""Sufficiency judge + tool planning / routing. Split from langchain_pipeline.py; behavior unchanged."""

import json
import logging

from typing import Any, Dict, List, Optional
from app.config import get_settings

from .shared import call_llm
from .models import Decision, default_decision
from .prompts import DECISION_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT
from .prompting import extract_json

logger = logging.getLogger(__name__)



async def judge_sufficiency(
    user_query: str,
    source_scope: str,
    evidence: Dict[str, Any],
    prior_clarification: Optional[str] = None,
    prior_data: Optional[Dict[str, Any]] = None,
) -> Decision:
    """LLM-as-decision-maker: tool call vs clarification vs answer planning.

    Never raises: any failure (transport, bad JSON, schema mismatch) falls
    back to the fail-open "answer" verdict so judging can never block a reply.
    """
    inventory = json.dumps(evidence, indent=2, default=str)
    prior_section = (
        f"PRIOR CLARIFICATION (asked last turn, never restate it):\n"
        f"{prior_clarification or 'none'}"
    )
    prior_data_section = (
        f"PRIOR DATA (previous answer's rows, use for follow-ups):\n"
        f"{json.dumps(prior_data, indent=2, default=str)}"
        if prior_data
        else "PRIOR DATA: none."
    )
    prompt = (
        f"User Query:\n{user_query}\n\n"
        f"Source scope: {source_scope}\n\n"
        f"Evidence inventory (tool outputs on hand):\n{inventory}\n\n"
        f"{prior_section}\n\n"
        f"{prior_data_section}\n\n"
        f"Respond strictly in the decision JSON schema."
    )
    try:
        content: Optional[str] = None
        for attempt in range(2):
            # Live models occasionally return an empty completion on the first
            # try despite HTTP 200, and long option lists can truncate the JSON
            # mid-string at a tight token budget (seen live: suggested_options
            # cut off, failing extraction and silently degrading to fail-open).
            # One retry with more headroom rescues the verdict instead.
            result = await call_llm(
                prompt=prompt,
                system_prompt=DECISION_SYSTEM_PROMPT,
                model=get_settings().groq_fast_model,
                temperature=0.0 if attempt == 0 else 0.3,
                max_tokens=800,
                json_mode=True,
            )
            content = (result.get("content") or "").strip()
            if not content:
                logger.warning(f"Sufficiency judge got empty content (attempt {attempt + 1}).")
                continue
            try:
                return Decision(**normalize_decision_payload(extract_json(content)))
            except Exception as parse_exc:
                logger.warning(
                    f"Sufficiency judge reply unparseable (attempt {attempt + 1}): {parse_exc}"
                )
                content = None
                continue
        if not content:
            raise ValueError("empty or unparseable judge reply after retry")
    except Exception as exc:
        logger.warning(f"Sufficiency judge failed, answering best-effort: {exc}")
        return default_decision()


# Tool catalog for judge-directed routing (see `plan_tools`). Name ->
# one-line description. The planner returns `tools_needed` from these keys;
# dispatch (`search_web`) runs exactly the planned adapters, falling back to
# the deterministic predicates when the plan is empty or invalid.
#
# Historical honesty: "market" is a ONE-MONTH series (short-term only) and
# "fundamentals" is a CURRENT snapshot (no dates). Neither can answer a
# multi-year question. "market_history" (multi-year weekly closes) and
# "financial_history" (annual revenue + net-income series) are the ONLY
# adapters that satisfy "last N years" stock / revenue / profitability asks.
TOOL_CATALOG: Dict[str, str] = {
    "snippets": "fresh web search snippets (general facts, news, opinions)",
    "market": "one-month Yahoo price series for a named company/entity (short-term only, never historical)",
    "fundamentals": "Yahoo company fundamentals snapshot: market cap, P/E, revenue (current only, no history)",
    "market_history": "multi-year Yahoo weekly price history for a named company (use for 'last N years' stock performance)",
    "financial_history": "annual Yahoo revenue + net-income history for a named company (use for multi-year revenue growth / profitability)",
    "wikipedia": "canonical Wikipedia summary for a named entity (who/what is X)",
    "macro": "FRED macro series: inflation/CPI, unemployment, rates, GDP",
    "extract": "full clean content of a URL mentioned verbatim in the query",
}


def normalize_tool_plan(value: Any) -> List[str]:
    """Coerce a ragged tools_needed value into catalog keys, order-kept."""
    if not isinstance(value, list):
        return []
    planned: List[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name in TOOL_CATALOG and name not in planned:
            planned.append(name)
    return planned


def normalize_decision_payload(payload: Any) -> dict:
    """Coerce a possibly ragged judge reply into Decision-shaped data."""
    if not isinstance(payload, dict):
        return {"decision": "answer"}
    normalized = {
        "decision": payload.get("decision", "answer"),
        "missing": payload.get("missing", "") or "",
        "chart_from_prior": bool(payload.get("chart_from_prior", False)),
        "visual_plan": payload.get("visual_plan", []) or [],
        "suggested_options": payload.get("suggested_options", []) or [],
        "preferred_visual": payload.get("preferred_visual") or None,
        "tools_needed": normalize_tool_plan(payload.get("tools_needed", [])),
    }
    if normalized["decision"] not in ("answer", "clarify"):
        normalized["decision"] = "answer"
    if normalized["preferred_visual"] not in (
        None, "table", "bar", "line", "pie", "area", "metric",
    ):
        normalized["preferred_visual"] = None
    return normalized


async def plan_tools(
    user_query: str,
    *,
    source_scope: str = "live_web",
    company_name: Optional[str] = None,
    prior_clarification: Optional[str] = None,
    has_tavily_key: bool = False,
    has_fred_key: bool = False,
) -> List[str]:
    """Judge-directed tool routing: which evidence adapters a live-web
    question needs, from the tool catalog. Never raises: any failure
    (transport, bad JSON, schema mismatch) returns [] meaning "no opinion",
    and dispatch falls back to the deterministic predicates - so a planner
    outage can never narrow or block an answer."""
    catalog_lines = "\n".join(
        f"- {name}: {description}" for name, description in TOOL_CATALOG.items()
    )
    system_prompt = PLAN_SYSTEM_PROMPT.replace("__CATALOG__", catalog_lines)
    context_lines = [f"Source scope: {source_scope}"]
    if company_name:
        context_lines.append(f"Company context: {company_name}")
    if prior_clarification:
        context_lines.append(f"Previously asked: {prior_clarification}")
    capabilities = (
        f"Capabilities: Tavily search/extract "
        f"{'available' if has_tavily_key else 'unavailable'}, "
        f"FRED macro series "
        f"{'available' if has_fred_key else 'unavailable'}."
    )
    prompt = (
        f"User Query:\n{user_query}\n\n"
        + "\n".join(context_lines)
        + f"\n{capabilities}\n\nRespond strictly in the routing JSON schema."
    )
    try:
        content: Optional[str] = None
        for attempt in range(2):
            result = await call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                model=get_settings().groq_fast_model,
                temperature=0.0 if attempt == 0 else 0.3,
                max_tokens=200,
                json_mode=True,
            )
            content = (result.get("content") or "").strip()
            if content:
                break
            logger.warning(f"Tool planner got empty content (attempt {attempt + 1}).")
        if not content:
            return []
        return normalize_tool_plan(extract_json(content).get("tools_needed", []))
    except Exception as exc:
        logger.warning(f"Tool planner failed, using default dispatch: {exc}")
        return []

__all__ = [
    "TOOL_CATALOG",
    "judge_sufficiency",
    "normalize_decision_payload",
    "normalize_tool_plan",
    "plan_tools",
]
