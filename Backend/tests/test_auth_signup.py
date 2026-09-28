"""POST /auth/signup robustness.

Two past production failures, both covered here with the same sqlite
TestClient pattern as test_auth_resend (dependency-overridden get_db, real
User rows):

1. A mail-server hiccup during signup used to turn the whole request into a
   500 even though the account was already committed — retrying then said
   "User already exists". Signup must succeed with tokens anyway; a lost
   link is recoverable via POST /auth/resend-verification.
2. The verification email linked to `/verify?token=`, but the frontend route
   is `/verify-email` — the link landed on the home page and users stayed
   unverified forever. The email body must contain the `/verify-email` path.
"""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/dummy")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-prod")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PORT", "25")
os.environ.setdefault("SMTP_USER", "u")
os.environ.setdefault("SMTP_PASS", "p")
os.environ.setdefault("EMAIL_FROM", "t@example.com")
os.environ.setdefault("CONTACT_FORM_RECIPIENT_EMAIL", "t@example.com")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client")

from app.main import app  # noqa: E402
from app.db.database import Base, get_db  # noqa: E402
import app.services.auth.email_auth as email_auth_module  # noqa: E402
import app.services.auth.email_sender as email_sender_module  # noqa: E402
from app.middlewares import auth_rate_limiter as limiter_module  # noqa: E402


@pytest.fixture(scope="module")
def db_engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("signup") / "app.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async def init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(init())
    try:
        yield engine
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture()
def client(db_engine, monkeypatch):
    maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    sender = AsyncMock()
    monkeypatch.setattr(email_auth_module, "send_verification_email", sender)
    limiter_module._buckets.clear()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client, sender
    finally:
        app.dependency_overrides.clear()


class TestSignup:
    def test_signup_succeeds_with_tokens(self, client):
        test_client, sender = client
        resp = test_client.post(
            "/auth/signup",
            json={
                "email": "newuser@example.com",
                "password": "password123",
                "name": "New",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user"]["email"] == "newuser@example.com"
        assert body["access_token"]
        assert body["refresh_token"]
        sender.assert_awaited_once()
        assert sender.await_args.args[0] == "newuser@example.com"

    def test_signup_succeeds_even_when_email_send_fails(self, client, monkeypatch):
        test_client, _sender = client
        failing = AsyncMock(side_effect=RuntimeError("SMTP down"))
        monkeypatch.setattr(email_auth_module, "send_verification_email", failing)

        resp = test_client.post(
            "/auth/signup",
            json={"email": "smtpdown@example.com", "password": "password123"},
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "smtpdown@example.com"
        assert resp.json()["access_token"]

    def test_signup_rejects_duplicate_email(self, client):
        test_client, _sender = client
        resp = test_client.post(
            "/auth/signup",
            json={"email": "newuser@example.com", "password": "password123"},
        )
        assert resp.status_code == 400

    def test_verification_link_points_to_verify_email_route(self, monkeypatch):
        """The emailed link must match the frontend `/verify-email` route —
        `/verify` lands on the home page and leaves users unverified."""
        import aiosmtplib

        captured = {}

        async def fake_send(message, **kwargs):
            captured["body"] = message.get_content()

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        monkeypatch.setattr(
            email_sender_module.settings, "FRONTEND_URL", "http://localhost:5173"
        )

        asyncio.run(
            email_sender_module.send_verification_email("u@example.com", "tok123")
        )
        assert "http://localhost:5173/verify-email?token=tok123" in captured["body"]
