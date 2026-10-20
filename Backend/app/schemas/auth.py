from pydantic import BaseModel, EmailStr, Field
from uuid import UUID
from typing import Optional
from pydantic import field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    # bcrypt only uses the first 72 bytes; cap the input so a multi-megabyte
    # password cannot be used to burn CPU before that truncation happens.
    password: str = Field(min_length=1, max_length=128)


class GoogleAuthRequest(BaseModel):
    token: str = Field(min_length=1, max_length=8192)


class GuestAuthRequest(BaseModel):
    # Required, not Optional: a missing device_id made the lookup filter on
    # `device_fingerprint IS NULL`, which matches every row that has a NULL
    # fingerprint (multiple such rows are legal), crashing the query with
    # MultipleResultsFound.
    #
    # The id is a bearer credential for the guest account, so bound its size
    # (unbounded input would be stored and indexed verbatim) and require enough
    # entropy to make guessing impractical. The frontend sends a UUID v4.
    device_id: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=8192)


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
    token: str = Field(min_length=1, max_length=8192)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v):
        if v and len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v
