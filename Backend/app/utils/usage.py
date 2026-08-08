from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from app.db.models.user import User

# Quota constants (specs/02): 4 questions per rolling 6h window, 100 lifetime,
# for every user - no plan branching (plan stays dormant for a future tier).
QUOTA_WINDOW_HOURS = 6
WINDOW_QUESTIONS_LIMIT = 4
LIFETIME_QUESTIONS_LIMIT = 100

QUOTA_WINDOW = timedelta(hours=QUOTA_WINDOW_HOURS)


def now_utc() -> datetime:
    """Timezone-aware UTC 'now', so every caller compares like-for-like."""
    return datetime.now(timezone.utc)


def window_elapsed_clause(now: datetime):
    """SQLAlchemy form of the window-rollover rule (single source of truth).

    This is the only place that decides "has this user's 6h window rolled over"
    for the atomic quota UPDATE in rate_limiter.py. A NULL window_started_at
    (brand-new or just-migrated user) counts as rolled, so their first request
    starts a fresh window instead of being counted against the old one.
    """
    return or_(
        User.window_started_at.is_(None),
        User.window_started_at <= now - QUOTA_WINDOW,
    )


def window_reset_at(window_started_at, now=None) -> datetime:
    """When the current window ends (used for the 429 reset-time message).

    Always compares in UTC with timezone-aware datetimes; a NULL window is a
    fresh window, so its reset time is a full window from now.
    """
    now = now or now_utc()
    if window_started_at is None:
        return now + QUOTA_WINDOW
    if window_started_at.tzinfo is None:
        window_started_at = window_started_at.replace(tzinfo=timezone.utc)
    return window_started_at + QUOTA_WINDOW