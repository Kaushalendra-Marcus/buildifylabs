from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models.user import User
from passlib.context import CryptContext
from app.config import get_settings
from app.services.auth.email_verification import create_verification_token
from app.services.auth.email_sender import send_verification_email
from app.services.auth.token_service import create_access_token, create_refresh_token

settings = get_settings()
pwd_manager = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_manager.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_manager.verify(plain, hashed)


async def register_user(db: AsyncSession, email, password, name):
    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        raise ValueError("User already exists")

    user = User(
        email=email,
        name=name,
        hashed_password=hash_password(password),
        auth_provider="email",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_verification_token(user.id)
    await send_verification_email(user.email, token)
    return {
        "user": user,
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
    }


async def login_user(db: AsyncSession, email, password):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user.is_email_verified:
        raise ValueError("Please verify your email first")

    if not user.is_active:
        raise ValueError("Account is disabled")

    return {
        "user": user,
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
    }
