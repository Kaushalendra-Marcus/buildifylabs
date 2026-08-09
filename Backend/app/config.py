from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "BACKEND"
    VERSION: str = "1.0.0"
    ALLOWED_ORIGIN: list[str] = ["http://localhost:5173"]

    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    SQL_ECHO: bool = False

    # These back features that are planned but not wired into any route yet
    # (LLM pipeline, embeddings/vector store, payments). Making them required
    # meant the app couldn't boot at all without dummy values for keys nothing
    # currently uses. They become required again as each feature actually
    # ships.
    GROQ_API_KEY: Optional[str] = Field(None, env="GROQ_API_KEY")
    # Interim model id — llama-3.1-70b-versatile is decommissioned (every call
    # fails and silently falls back to HF). This model is itself scheduled to
    # retire 2026-08-16; needs a durable choice before then (see plan B5).
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    HF_API_KEY: Optional[str] = Field(None, env="HF_API_KEY")
    HF_MODEL: str = "mistralai/Mixtral-8x7B-Instruct-v0.1"

    PINECONE_API_KEY: Optional[str] = Field(None, env="PINECONE_API_KEY")
    PINECONE_ENVIRONMENT: Optional[str] = Field(None, env="PINECONE_ENVIRONMENT")

    REDIS_URL: Optional[str] = Field(None, env="REDIS_URL")

    JWT_SECRET: str = Field(..., env="JWT_SECRET")
    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    FRONTEND_URL: str = Field(..., env="FRONTEND_URL")

    SMTP_HOST: str = Field(..., env="SMTP_HOST")
    SMTP_PORT: int = Field(..., env="SMTP_PORT")
    SMTP_USER: str = Field(..., env="SMTP_USER")
    SMTP_PASS: str = Field(..., env="SMTP_PASS")
    EMAIL_FROM: str = Field(..., env="EMAIL_FROM")

    # Recipient for the POST /contact lead-capture form (specs/02 §2 FR5).
    # A config value rather than a hardcoded address, so it can be changed
    # without a code edit. Required because /contact ships with this phase.
    CONTACT_FORM_RECIPIENT_EMAIL: str = Field(..., env="CONTACT_FORM_RECIPIENT_EMAIL")

    # Google login is live and uses GOOGLE_CLIENT_ID to verify ID tokens.
    # GOOGLE_CLIENT_SECRET isn't referenced anywhere yet (only needed for a
    # server-side auth-code exchange flow, which isn't implemented), so it
    # stays optional until that's built.
    GOOGLE_CLIENT_ID: str = Field(..., env="GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = Field(None, env="GOOGLE_CLIENT_SECRET")

    HUGGINGFACE_MODEL_PATH: str = "sentence-transformers/all-MiniLM-L6-v2"

    UPI_ID: Optional[str] = Field(None, env="UPI_ID")
    PAYMENT_AMOUNT: int = 299

    LOGIN_RATE_LIMIT: int = 5
    VERIFY_EMAIL_RATE_LIMIT: int = 3

    # Where uploaded raw files are persisted (Phase B3 storage backend, gap #4):
    # local disk for dev; swap storage.py for an object-store backend in prod.
    # Optional with a sane default, so upload works without extra env setup.
    UPLOAD_DIR: str = "data/uploads"

    REQUESTS_PER_MINUTE: int = 60

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
