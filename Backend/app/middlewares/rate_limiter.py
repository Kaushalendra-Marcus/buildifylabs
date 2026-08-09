import logging
from datetime import datetime, timezone

from fastapi import Depends
from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.user import User
from app.middlewares.auth_middleware import get_current_user
from app.utils import usage

logger = logging.getLogger(__name__)


class QuotaLimitExceeded(Exception):
    """429 with an explicit body (429 {detail, contact_form?}).

    Raised by the rate limiter; handled by an app-level exception handler
    (see app/main.py) so the response body matches the specs/02 §3 contract
    exactly - including a top-level ``contact_form`` flag on the lifetime cap,
    which a plain HTTPException(detail=...) can't express.
    """

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload)


async def rate_limiter(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    # One atomic conditional UPDATE over BOTH counters plus the window
    # rollover, so:
    #   - the check-and-increment is a single statement (row-level serized by
    #     Postgres) - two concurrent requests can't both read under the limit,
    #     and
    #   - a request arriving right as the window rolls can't count against the
    #     old window after window_started_at has been advanced (the roll and
    #     the increment happen in the same statement).
    # Rolling variables: when the window has elapsed (or never started) the
    # count resets to 1 and window_started_at moves to now, as one action.
    now = datetime.now(timezone.utc)
    window_rolled = usage.window_elapsed_clause(now)

    result = await db.execute(
        update(User)
        .where(
            User.id == user.id,
            User.questions_lifetime < usage.LIFETIME_QUESTIONS_LIMIT,
            or_(window_rolled, User.questions_in_window < usage.WINDOW_QUESTIONS_LIMIT),
        )
        .values(
            questions_in_window=case(
                (window_rolled, 1), else_=User.questions_in_window + 1
            ),
            window_started_at=case((window_rolled, now), else_=User.window_started_at),
            questions_lifetime=User.questions_lifetime + 1,
        )
        .returning(
            User.questions_in_window, User.questions_lifetime, User.window_started_at
        )
        # synchronize_session=False: this is a pure-Core UPDATE - the generated SQL
        # is untouched, but SQLAlchemy won't try to re-evaluate the CASE/WHERE in
        # Python against in-session objects (which compares a DB-loaded naive
        # window_started_at against the tz-aware `now` and crashes on SQLite).
        .execution_options(synchronize_session=False)
    )
    granted = result.first()

    if granted is None:
        await db.commit()

        # The UPDATE already denied the request atomically; this read only
        # decides WHICH message to show (lifetime vs. window), so its being a
        # separate SELECT is not a quota race - the denial itself cannot be
        # undone by it.
        current = (
            await db.execute(select(User).where(User.id == user.id))
        ).scalar_one_or_none()

        if (
            current is not None
            and current.questions_lifetime >= usage.LIFETIME_QUESTIONS_LIMIT
        ):
            raise QuotaLimitExceeded(
                {
                    "detail": (
                        f"You've reached the {usage.LIFETIME_QUESTIONS_LIMIT}-question "
                        "limit for now."
                    ),
                    "contact_form": True,
                }
            )

        reset_at = usage.window_reset_at(
            current.window_started_at if current is not None else None
        ).strftime("%Y-%m-%d %H:%M UTC")
        raise QuotaLimitExceeded(
            {
                "detail": (
                    f"You've used your {usage.WINDOW_QUESTIONS_LIMIT} questions for "
                    f"this {usage.QUOTA_WINDOW_HOURS}-hour window. More unlock at "
                    f"{reset_at}."
                )
            }
        )

    await db.commit()
    await db.refresh(user)

    return user