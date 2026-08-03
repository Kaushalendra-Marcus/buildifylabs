from datetime import datetime, timezone

from app.db.models.user import User


def reset_daily_usage_if_needed(user: User) -> bool:
    """Reset a user's daily query counter when the UTC calendar day has rolled over.

    This is the single source of truth for the "has this user's daily quota
    reset" rule. It used to be duplicated (with two different reset rules -
    a rolling 24h window in guest_auth.py vs. a calendar-day check in
    rate_limiter.py) and mixed naive/timezone-aware datetimes, which could
    raise "can't subtract offset-naive and offset-aware datetimes".

    Always compares in UTC using timezone-aware datetimes. Mutates `user` in
    place and returns True if a reset happened, so the caller knows whether
    it needs to commit.
    """
    now = datetime.now(timezone.utc)
    last_reset = user.last_reset

    if last_reset is not None and last_reset.tzinfo is None:
        last_reset = last_reset.replace(tzinfo=timezone.utc)

    if last_reset is None or last_reset.date() != now.date():
        user.queries_today = 0
        user.last_reset = now
        return True

    return False
