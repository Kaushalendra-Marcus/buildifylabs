from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from uuid import UUID
from datetime import datetime


class UserBase(BaseModel):
    email: Optional[EmailStr] = None
    name: Optional[str] = None


class UserCreate(UserBase):
    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if v and len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: UUID
    plan: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None