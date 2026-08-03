from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from google.oauth2 import id_token
from google.auth.transport import requests

from app.db.models.user import User
from app.services.auth.token_service import create_access_token, create_refresh_token
from app.config import get_settings

settings = get_settings()


async def google_login(db: AsyncSession, token: str):
    try:
        idinfo = id_token.verify_oauth2_token(
            token, requests.Request(), settings.GOOGLE_CLIENT_ID
        )

        google_id = idinfo.get("sub")
        email = idinfo.get("email")
        name = idinfo.get("name")

        if not google_id:
            raise ValueError("Invalid Google token")

        if not email:
            raise HTTPException(status_code=400, detail="Email not provided by Google")

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google token verification failed",
        )

    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user:
            user.google_id = google_id
            user.auth_provider = "google"
            # Google has already verified this address, so treat the account
            # as email-verified too instead of leaving it stuck unverified.
            user.is_verified = True
            await db.commit()
            await db.refresh(user)

    if user and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    if not user:
        user = User(
            email=email,
            name=name,
            google_id=google_id,
            auth_provider="google",
            is_active=True,
            is_verified=True,
            plan="free",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
