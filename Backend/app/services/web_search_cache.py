"""Repeat-query cache for live-web evidence (specs/07 FR5).

Same external question asked minutes apart must not re-scrape from scratch:
that costs latency, provider spend, and answer consistency (no guarantee of
getting the same evidence twice). Cache the merged WebSearchResult payload
for WEB_SEARCH_CACHE_TTL_SECONDS (default 6h).

Backend: Redis when REDIS_URL is set and the `redis` package is installed;
otherwise a process-local in-memory dict with expiry. Both paths are
fail-soft: any cache error degrades to "no cache", never a request failure.
"""
import hashlib
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_memory_cache: dict[str, tuple[float, dict]] = {}

_redis_client: Any = None
_redis_init_attempted = False


SCHEMA_VERSION = "evidence-v1"
PARSER_VERSION = "parser-v1"

# Separate TTL policy per evidence class (P0#21): news/intraday short,
# historical/fundamentals medium, stable research long.
TTL_BY_CLASS: dict[str, int] = {
    "news": 900,  # 15 min
    "intraday": 900,
    "historical": 21600,  # 6h
    "fundamentals": 21600,
    "research": 86400,  # 24h stable research
    "default": 21600,
}


def ttl_for_evidence_class(evidence_class: Optional[str] = None) -> int:
    try:
        return int(TTL_BY_CLASS.get(str(evidence_class or "default"), TTL_BY_CLASS["default"]))
    except Exception:
        return int(TTL_BY_CLASS["default"])


def make_cache_key(
    queries: list[str],
    entities: list[str],
    company_name: Optional[str] = None,
    time_sensitive: bool = False,
    planned_tools: Optional[list[str]] = None,
    *,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    timeframe_label: Optional[str] = None,
    evidence_class: Optional[str] = None,
    provider_version: Optional[str] = None,
    schema_version: Optional[str] = None,
    parser_version: Optional[str] = None,
) -> str:
    """Versioned, scoped cache identity (P0#21).

    Accounts for query + entities + timeframe/history window + required
    evidence class (planned tools) + provider state + evidence schema +
    parser version + user scope (user-specific evidence never shared
    across users). Backward compatible: new dimensions default so old
    callers keep working (user-gated dimensions empty -> global key only
    for public evidence; user-specific callers must pass user_id).
    """
    normalized = json.dumps(
        {
            "q": sorted(" ".join(q.lower().split()) for q in queries if q.strip()),
            "e": sorted(e.lower().strip() for e in entities if e.strip()),
            "c": (company_name or "").lower().strip(),
            "t": bool(time_sensitive),
            "p": sorted(
                str(tool).strip().lower()
                for tool in (planned_tools or [])
                if str(tool).strip()
            ),
            "u": str(user_id or "").strip(),
            "th": str(thread_id or "").strip(),
            "tf": str(timeframe_label or "").strip().lower(),
            "ec": str(evidence_class or "").strip().lower(),
            "pv": str(provider_version or "").strip(),
            "sv": str(schema_version or SCHEMA_VERSION),
            "pv2": str(parser_version or PARSER_VERSION),
        },
        sort_keys=True,
    )
    return "websearch:" + hashlib.sha256(normalized.encode()).hexdigest()[:32]


def is_cached_payload_valid(payload: Any) -> bool:
    """A cache hit must still be valid under the current evidence schema."""
    try:
        if not isinstance(payload, dict):
            return False
        if payload.get("schema_version") not in (None, SCHEMA_VERSION):
            # Versioned payloads from another schema generation are stale.
            return payload.get("schema_version") == SCHEMA_VERSION
        return True
    except Exception:
        return False


def _get_redis():
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    _redis_init_attempted = True
    try:
        from app.config import get_settings

        url = get_settings().REDIS_URL
        if not url:
            return None
        import redis.asyncio as redis  # type: ignore

        _redis_client = redis.from_url(url, decode_responses=True)
        return _redis_client
    except Exception as exc:
        logger.warning("Web-search Redis unavailable, using memory cache: %s", exc)
        _redis_client = None
        return None


def _reset_cache_state() -> None:
    """Test seam: clear memory entries and force Redis re-init."""
    global _redis_client, _redis_init_attempted
    _memory_cache.clear()
    _redis_client = None
    _redis_init_attempted = False


async def get_cached_result(key: str) -> Optional[dict]:
    client = _get_redis()
    if client is not None:
        try:
            raw = await client.get(key)
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("Web-search cache read failed: %s", exc)
            return None
        return None
    entry = _memory_cache.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if expires_at < time.monotonic():
        _memory_cache.pop(key, None)
        return None
    return value


async def set_cached_result(key: str, value: dict, ttl_seconds: Optional[int] = None) -> None:
    try:
        from app.config import get_settings

        ttl = ttl_seconds or get_settings().WEB_SEARCH_CACHE_TTL_SECONDS
    except Exception:
        ttl = 21600
    client = _get_redis()
    if client is not None:
        try:
            await client.setex(key, ttl, json.dumps(value, default=str))
            return
        except Exception as exc:
            logger.warning("Web-search cache write failed: %s", exc)
            return
    _memory_cache[key] = (time.monotonic() + ttl, value)
