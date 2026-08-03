from pydantic import BaseModel, EmailStr
from uuid import UUID
from typing import Optional
from pydantic import field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    token: str


class GuestAuthRequest(BaseModel):
    device_id: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class AuthUserResponse(BaseModel):
    id: UUID
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    plan: str

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    user: AuthUserResponse
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v):
        if v and len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v
