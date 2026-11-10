"""Per-request provider spend ledger.

The Firecrawl quota was exhausted before anyone could say why, because nothing
recorded what a request actually cost. This keeps a small counter set per
request (LLM calls + tokens, Tavily searches/extracts, Firecrawl searches and
estimated credits, web-cache hit/miss). The chat route attaches the snapshot to
``research_state["cost"]``, which is persisted in QueryLogs, and
``scripts/cost_report.py`` rolls it up per day and per user.

State lives in a ContextVar holding a MUTABLE dict: provider calls run inside
asyncio.gather child tasks, and mutating the shared dict is visible to all of
them (rebinding a value in a child task would not be). Outside a request every
call is a silent no-op, and nothing here ever raises.
"""

from contextvars import ContextVar
from typing import Any, Dict, Optional

_COUNTERS = (
    "llm_calls",
    "prompt_tokens",
    "completion_tokens",
    "tavily_searches",
    "tavily_extracts",
    "firecrawl_searches",
    "firecrawl_credits",
)

_ledger: ContextVar[Optional[Dict[str, Any]]] = ContextVar("cost_ledger", default=None)


def begin_request() -> None:
    """Start a fresh ledger for the current request."""
    try:
        state: Dict[str, Any] = {name: 0 for name in _COUNTERS}
        state["web_cache"] = ""  # "" = not used, "hit", "miss", "off"
        _ledger.set(state)
    except Exception:
        pass


def add(name: str, amount: int = 1) -> None:
    try:
        state = _ledger.get()
        if state is not None:
            state[name] = int(state.get(name, 0) or 0) + int(amount)
    except Exception:
        pass


def set_flag(name: str, value: str) -> None:
    try:
        state = _ledger.get()
        if state is not None:
            state[name] = value
    except Exception:
        pass


def snapshot() -> Dict[str, Any]:
    """Copy of the current request's ledger ({} outside a request)."""
    try:
        state = _ledger.get()
        return dict(state) if state is not None else {}
    except Exception:
        return {}


def _usage_field(usage: Any, key: str) -> int:
    try:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    except Exception:
        return 0


def record_llm_call(result: Any) -> None:
    """Count one LLM call and add its token usage when the result carries it."""
    try:
        add("llm_calls", 1)
        usage = (
            result.get("usage") if isinstance(result, dict) else getattr(result, "usage", None)
        )
        if usage:
            add("prompt_tokens", _usage_field(usage, "prompt_tokens"))
            add("completion_tokens", _usage_field(usage, "completion_tokens"))
    except Exception:
        pass


__all__ = ["add", "begin_request", "record_llm_call", "set_flag", "snapshot"]
