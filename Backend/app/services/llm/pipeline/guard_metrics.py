"""Hallucination-guard bookkeeping: per-request events + process counters.

Every guard that fires (an ungrounded number, an unsupported citation, a
fail-closed cap, a neutralized prompt-injection line, ...) calls
``record_guard``. Two things happen:

* the event is appended to a per-request list (a ContextVar, so concurrent
  requests never mix) that the pipeline attaches to
  ``PipelineOutput.research_state["guards"]`` -- persisted in QueryLogs, which
  is what ``scripts/guard_report.py`` aggregates into fire-rates;
* a process-wide counter is bumped and one INFO log line is written.

The ContextVar also carries the per-request *data profile* (row count, date
range, ...). It is passed this way instead of as a new ``run_pipeline``
argument on purpose: many tests replace ``run_pipeline`` / ``_narrate`` with
fakes whose signatures must not change.

Nothing in here ever raises.
"""

import logging
import threading
from collections import Counter
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_EVENTS_PER_REQUEST = 25

_counters: Counter = Counter()
_lock = threading.Lock()
_events: ContextVar[Optional[List[str]]] = ContextVar("guard_events", default=None)
_profile: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "guard_data_profile", default=None
)


def begin_request() -> None:
    """Start a fresh per-request event list (call at the top of run_pipeline)."""
    try:
        _events.set([])
    except Exception:
        pass


def record_guard(name: str, detail: str = "") -> None:
    """Record that guard ``name`` fired. ``detail`` is short free text."""
    try:
        with _lock:
            _counters[name] += 1
        events = _events.get()
        if events is not None and len(events) < _MAX_EVENTS_PER_REQUEST:
            events.append(f"{name}: {detail[:120]}" if detail else name)
        logger.info("GUARD %s %s", name, (detail or "")[:160])
    except Exception:
        pass


def request_events() -> List[str]:
    """Guard events recorded so far for the current request."""
    try:
        return list(_events.get() or [])
    except Exception:
        return []


def snapshot() -> Dict[str, int]:
    """Process-wide guard counters (for logs / debugging)."""
    try:
        with _lock:
            return dict(_counters)
    except Exception:
        return {}


def reset_counters() -> None:
    """Test helper."""
    try:
        with _lock:
            _counters.clear()
    except Exception:
        pass


def set_data_profile(profile: Optional[Dict[str, Any]]) -> None:
    """Make this request's data profile visible to the narration step."""
    try:
        _profile.set(profile or None)
    except Exception:
        pass


def get_data_profile() -> Optional[Dict[str, Any]]:
    try:
        return _profile.get()
    except Exception:
        return None


__all__ = [
    "begin_request",
    "get_data_profile",
    "record_guard",
    "request_events",
    "reset_counters",
    "set_data_profile",
    "snapshot",
]
