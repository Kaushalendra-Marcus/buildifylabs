from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from app.config import get_settings

settings = get_settings()


def create_verification_token(user_id):
    payload = {
        # jose's jwt.encode() JSON-serializes this payload, and a raw UUID
        # object isn't JSON serializable - always pass the string form.
        "sub": str(user_id),
        "type": "verify",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=20),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_email_token(token: str):
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )

        if payload.get("type") != "verify":
            raise HTTPException(status_code=400, detail="Invalid token")

        return payload.get("sub")

    except JWTError:
        raise HTTPException(status_code=400, detail="Token expired or invalid")


def create_password_reset_token(user_id):
    payload = {
        "sub": str(user_id),
        "type": "reset_password",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=20),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_password_reset_token(token: str):
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )

        if payload.get("type") != "reset_password":
            raise HTTPException(status_code=400, detail="Invalid token")

        return payload.get("sub")

    except JWTError:
        raise HTTPException(status_code=400, detail="Token expired or invalid")
