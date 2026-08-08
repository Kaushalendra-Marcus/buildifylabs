from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from datetime import datetime, timezone

from app.db.models.user import User
from app.services.auth.token_service import create_access_token, create_refresh_token
from app.utils.usage import reset_daily_usage_if_needed

GUEST_DAILY_LIMIT = 2


async def create_guest_user(db: AsyncSession, fingerprint: str):
    if not fingerprint:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Guest access requires a device id",
        )

    result = await db.execute(
        select(User).where(User.device_fingerprint == fingerprint)
    )
    existing = result.scalar_one_or_none()

    if existing:
        if reset_daily_usage_if_needed(existing):
            await db.commit()

        if existing.queries_today >= GUEST_DAILY_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guest limit reached. Please sign up to continue.",
            )

        return {
            "user": existing,
            "access_token": create_access_token(existing.id),
            "refresh_token": create_refresh_token(existing.id),
        }

    user = User(
        auth_provider="guest",
        is_active=True,
        plan="free",
        device_fingerprint=fingerprint,
        queries_today=0,
        last_reset=datetime.now(timezone.utc),
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    return {
        "user": user,
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
    }
