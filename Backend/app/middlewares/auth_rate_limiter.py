import time

from fastapi import HTTPException, Request, status

from app.config import get_settings

settings = get_settings()

# The config only defines attempt *counts* (LOGIN_RATE_LIMIT, VERIFY_EMAIL_RATE_LIMIT);
# the window is a decision this module makes. A one-hour rolling window keeps the
# endpoints from being brute-forced without locking out a legitimate user who
# mistypes a password a few times.
RATE_LIMIT_WINDOW_SECONDS = 3600
MAX_TRACKED_KEYS = 10000

# In-memory sliding window. Process-local on purpose: Redis is a deferred
# dependency (plan B7), so this is per-instance. Documented MVP limitation —
# swap for a shared store when the app runs more than one worker.
_buckets: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(key: str, limit: int) -> None:
    now = time.time()
    recent = [ts for ts in _buckets.get(key, []) if now - ts <= RATE_LIMIT_WINDOW_SECONDS]

    if len(recent) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    recent.append(now)
    _buckets[key] = recent

    if len(_buckets) > MAX_TRACKED_KEYS:
        for k in list(_buckets):
            _buckets[k] = [
                ts for ts in _buckets[k] if now - ts <= RATE_LIMIT_WINDOW_SECONDS
            ]
            if not _buckets[k]:
                del _buckets[k]


async def login_rate_limit(request: Request) -> None:
    try:
        body = await request.json()
    except Exception:
        body = None
    email = (body.get("email") or "").lower() if isinstance(body, dict) else ""
    _check_rate_limit(f"login:{_client_ip(request)}:{email}", settings.LOGIN_RATE_LIMIT)


async def verify_email_rate_limit(request: Request) -> None:
    _check_rate_limit(f"verify:{_client_ip(request)}", settings.VERIFY_EMAIL_RATE_LIMIT)
