from fastapi import APIRouter, Depends, HTTPException
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.user import User
from app.schemas.user import UserCreate
from app.schemas.auth import LoginRequest
from app.services.auth.email_auth import register_user, login_user, hash_password
from app.services.auth.guest_auth import create_guest_user
from app.services.auth.google_auth import google_login
from app.services.auth.email_verification import (
    verify_email_token,
    create_password_reset_token,
    verify_password_reset_token,
)
from app.services.auth.email_sender import send_password_reset_email
from app.services.auth.token_service import (
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
)
from app.middlewares.auth_rate_limiter import login_rate_limit, verify_email_rate_limit
from app.schemas.auth import GuestAuthRequest
from app.schemas.auth import GoogleAuthRequest
from app.schemas.auth import TokenResponse
from app.schemas.auth import AuthResponse
from app.schemas.auth import RefreshTokenRequest
from app.schemas.auth import ForgotPasswordRequest
from app.schemas.auth import ResetPasswordRequest
from sqlalchemy import select
from uuid import UUID

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/signup", response_model=AuthResponse)
async def signup(data: UserCreate, db: AsyncSession = Depends(get_db)):
    # register_user raises ValueError for business-rule rejections (already
    # exists, weak password handled at the schema layer) — surface those as
    # clean 400s. Anything else (a dropped DB connection, a bug) must propagate
    # as a real 500, never a 400 with the raw exception text leaked.
    try:
        return await register_user(db, data.email, data.password, data.name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/signin", response_model=AuthResponse, dependencies=[Depends(login_rate_limit)])
async def signin(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await login_user(db, data.email, data.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/guest", response_model=AuthResponse)
async def guest(data: GuestAuthRequest, db: AsyncSession = Depends(get_db)):
    return await create_guest_user(db, data.device_id)


@router.post("/google", response_model=AuthResponse)
async def google(data: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    return await google_login(db, data.token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    payload = verify_refresh_token(data.refresh_token)

    try:
        user_id = UUID(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    return {
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
    }


@router.get("/verify-email", dependencies=[Depends(verify_email_rate_limit)])
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    user_id = UUID(verify_email_token(token))
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    user.is_verified = True
    await db.commit()
    await db.refresh(user)

    return {"message": "Email verified successfully"}


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    # Only email/password accounts have a password to reset, and we return
    # the same generic message either way so this endpoint can't be used to
    # find out which emails are registered.
    if user and user.auth_provider == "email":
        token = create_password_reset_token(user.id)
        await send_password_reset_email(user.email, token)

    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    user_id = UUID(verify_password_reset_token(data.token))

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    user.hashed_password = hash_password(data.new_password)
    await db.commit()

    return {"message": "Password reset successfully"}
