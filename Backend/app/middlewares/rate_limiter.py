from fastapi import Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.user import User
from app.middlewares.auth_middleware import get_current_user
from app.utils.usage import reset_daily_usage_if_needed

LIMITS = {"guest": 2, "free": 4, "pro": 40}


async def rate_limiter(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if reset_daily_usage_if_needed(user):
        await db.commit()

    if user.auth_provider == "guest":
        limit = LIMITS["guest"]
    else:
        limit = LIMITS.get(user.plan, LIMITS["free"])

    # Check-then-increment as two separate statements is a race condition:
    # two concurrent requests can both read queries_today under the limit
    # before either commits, letting both through. Doing the check and the
    # increment as a single conditional UPDATE makes Postgres serialize it
    # at the row level, so only requests that still fit under the limit at
    # the moment of the write succeed.
    result = await db.execute(
        update(User)
        .where(User.id == user.id, User.queries_today < limit)
        .values(queries_today=User.queries_today + 1)
        .returning(User.queries_today)
    )
    updated = result.first()
    await db.commit()

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily limit reached. Upgrade your plan.",
        )

    user.queries_today = updated[0]

    return user
