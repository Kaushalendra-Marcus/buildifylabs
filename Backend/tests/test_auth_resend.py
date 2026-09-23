"""POST /auth/resend-verification (auth gap: lost/expired links had no
self-serve recovery).

Same sqlite TestClient pattern as test_files_api (dependency-overridden
get_db, real User rows): the endpoint always returns the same generic
message (anti-enumeration, like /forgot-password) and only sends a new
link to unverified email/password accounts. The SMTP sender is
monkeypatched — no mail server needed.
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
from app.db.models.user import User  # noqa: E402
from app.middlewares import auth_rate_limiter as limiter_module  # noqa: E402
import app.routes.auth as auth_routes  # noqa: E402

GENERIC = "registered and unverified"


@pytest.fixture(scope="module")
def db_engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("resend") / "app.db"
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
    monkeypatch.setattr(auth_routes, "send_verification_email", sender)
    limiter_module._buckets.clear()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client, sender
    finally:
        app.dependency_overrides.clear()


def make_user(db_engine, email, *, verified=False, provider="email"):
    async def create():
        maker = async_sessionmaker(db_engine, expire_on_commit=False)
        async with maker() as session:
            session.add(
                User(
                    email=email,
                    auth_provider=provider,
                    hashed_password="hashed",
                    is_verified=verified,
                )
            )
            await session.commit()

    asyncio.run(create())


class TestResendVerification:
    def test_unverified_user_gets_new_link(self, client, db_engine):
        test_client, sender = client
        make_user(db_engine, "unverified@example.com")

        resp = test_client.post(
            "/auth/resend-verification", json={"email": "unverified@example.com"}
        )
        assert resp.status_code == 200
        assert GENERIC in resp.json()["message"]
        sender.assert_awaited_once()
        assert sender.await_args.args[0] == "unverified@example.com"
        assert sender.await_args.args[1]  # a fresh token

    def test_unknown_email_same_message_no_send(self, client):
        test_client, sender = client
        resp = test_client.post(
            "/auth/resend-verification", json={"email": "nobody@example.com"}
        )
        assert resp.status_code == 200
        assert GENERIC in resp.json()["message"]
        sender.assert_not_awaited()

    def test_verified_user_no_new_link(self, client, db_engine):
        test_client, sender = client
        make_user(db_engine, "verified@example.com", verified=True)

        resp = test_client.post(
            "/auth/resend-verification", json={"email": "verified@example.com"}
        )
        assert resp.status_code == 200
        assert GENERIC in resp.json()["message"]
        sender.assert_not_awaited()
