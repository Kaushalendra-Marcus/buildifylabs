"""LLM query framing for live-web search (specs/07 support, generic).

The raw user query is a poor search query: it carries chat phrasing, glued-on
clarification answers ("query - option - option"), and no retrieval strategy.
This module reframes it into 1-3 clean search queries before any HTTP happens.

Generic by design: no per-topic rules live here. The only standing strategy is
source diversity - when the question seeks opinions, experiences, rankings, or
fast-moving facts, one variant targets community discussion (Reddit, X,
forums), because those surface what polished pages omit. Everything
topic-specific comes from the query itself.

Fails soft: any LLM/parse failure returns [raw_query] so search behaves exactly
as before the rewrite step existed.
"""
import json
import logging
import re
from typing import Any, Optional

from app.services.llm.groq_service import generate_response
from app.config import get_settings

logger = logging.getLogger(__name__)

# Time-sensitive intent: the model has no recency signal without this, so
# "latest/current/this week" questions need topic="news" + time_range upstream.
TIME_SENSITIVE_RE = re.compile(
    r"\b(latest|current|today|now|breaking|live|real[\s-]?time|update[sd]?|"
    r"recent|this\s+(week|month|year|quarter)|past\s+\d+\s+days?|"
    r"news|price right now)\b|\b20(2[4-9]|3\d)\b",
    re.IGNORECASE,
)


def is_time_sensitive_query(text: str) -> bool:
    """Deterministic recency gate (no LLM needed)."""
    return bool(text and TIME_SENSITIVE_RE.search(text))

REWRITE_SYSTEM_PROMPT = """You frame web-search queries. You do NOT answer the user.

You receive a chat message (possibly with appended clarification answers joined
by " - ") and optional prior context. Extract what to search for and return
1-3 short search queries as JSON:

{"queries": ["primary query", "optional second angle"], "entities": ["named things"], "time_sensitive": false}

Rules (all generic, no topic special-casing):
- Strip chat phrasing ("can you tell me", "please", "show in chart form") and
  merge any appended clarification answers into the intent (they refine it).
- Keep named entities, numbers, comparisons, and time bounds verbatim.
- Expand to at most 3 queries only when genuinely different angles help
  (e.g. a general angle plus a discussion/experience angle).
- When the question seeks opinions, experiences, rankings, controversies, or
  fast-moving facts, make one variant target community discussion with a
  site: restriction (site:reddit.com, site:x.com, or site:indiehackers.com -
  pick at most one, whichever fits the question best).
- Set "time_sensitive" true when the question asks about recency ("latest",
  "current", "today", "this week/month", "news", "price right now", a year
  like 2025/2026) - false otherwise.
- Never invent entities, dates, or numbers not present in the message.
- Return ONLY the JSON object, no other text."""

MAX_REWRITE_QUERIES = 3


def _coerce_rewrite_payload(payload: Any, raw_query: str) -> dict:
    if not isinstance(payload, dict):
        return {
            "queries": [raw_query],
            "entities": [],
            "time_sensitive": is_time_sensitive_query(raw_query),
        }
    queries = payload.get("queries")
    if not isinstance(queries, list):
        queries = [raw_query]
    cleaned = [str(item).strip() for item in queries if str(item).strip()]
    entities = payload.get("entities")
    if not isinstance(entities, list):
        entities = []
    coerced_queries = cleaned[:MAX_REWRITE_QUERIES] or [raw_query]
    flag = payload.get("time_sensitive")
    if not isinstance(flag, bool):
        # Deterministic backstop: regex over the raw message + framed queries
        # so a model that omits the flag still gets recency routing.
        flag = is_time_sensitive_query(
            raw_query + " " + " ".join(coerced_queries)
        )
    return {
        "queries": coerced_queries,
        "entities": [str(item).strip() for item in entities if str(item).strip()],
        "time_sensitive": flag,
    }


async def rewrite_search_queries(
    user_query: str,
    prior_clarification: Optional[str] = None,
    company_name: Optional[str] = None,
) -> dict:
    """Frame 1-3 search queries from a chat message. Never raises."""
    context_lines = []
    if company_name:
        context_lines.append(f"Company context: {company_name}")
    if prior_clarification:
        context_lines.append(f"Previously asked: {prior_clarification}")
    context = "\n".join(context_lines) or "none"
    prompt = (
        f"Chat message:\n{user_query}\n\nContext:\n{context}\n\n"
        f"Return ONLY the JSON object."
    )
    try:
        result = await generate_response(
            prompt=prompt,
            system_prompt=REWRITE_SYSTEM_PROMPT,
            model=get_settings().groq_fast_model,
            temperature=0.0,
            max_tokens=300,
            json_mode=True,
        )
        content = (result.get("content") or "").strip()
        if not content:
            raise ValueError("empty rewrite reply")
        text = content.replace("```json", "").replace("```", "").strip()
        return _coerce_rewrite_payload(json.loads(text), user_query)
    except Exception as exc:
        logger.warning(f"Query rewrite failed, searching raw query: {exc}")
        return {
            "queries": [user_query],
            "entities": [],
            "time_sensitive": is_time_sensitive_query(user_query),
        }
