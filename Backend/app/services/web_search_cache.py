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


def make_cache_key(
    queries: list[str],
    entities: list[str],
    company_name: Optional[str] = None,
    time_sensitive: bool = False,
    planned_tools: Optional[list[str]] = None,
) -> str:
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
        },
        sort_keys=True,
    )
    return "websearch:" + hashlib.sha256(normalized.encode()).hexdigest()[:32]


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
