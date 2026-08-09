"""End-to-end tests for POST /chat and POST /chat/flag (Phase B4).

Uses FastAPI's TestClient with dependency overrides: a real file-backed SQLite
DB seeded with a user, a completed FileUpload, and the user's per-user data
table. The LLM is mocked at both call sites (SQL generation in
`app.routes.chat.generate_response` and the pipeline narration in
`app.services.llm.langchain_pipeline.generate_response`), so the whole loop
runs without any provider.

Covers the B4 acceptance + trust requirements: a working /chat loop, real
traceability fields (sql_query / data_preview / query_log_id), QueryLogs
written per query, the flag endpoint (own-only), the INVALID_QUERY graceful
message, the clarification alternate mode, and quota 429 via rate_limiter.
"""

import asyncio
import json
import os
import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("HF_API_KEY", "test-hf-key")

from app.main import app  # noqa: E402
from app.db.database import Base, get_db  # noqa: E402
from app.db.models.file_upload import FileUpload  # noqa: E402
from app.db.models.query_logs import QueryLogs  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.middlewares.auth_middleware import get_current_user  # noqa: E402
from app.services.data.executor import user_data_table_name  # noqa: E402

TEST_ID = uuid.UUID("aaaaaaaa-1111-2222-3333-444455556666")
TEST_ID_QUOTA = uuid.UUID("dddddddd-1111-2222-3333-444455556666")
TEST_ID_NO_DATA = uuid.UUID("cccccccc-1111-2222-3333-444455556666")
TEST_ID_OTHER = uuid.UUID("bbbbbbbb-1111-2222-3333-444455556666")
OTHER_LOG_ID = uuid.UUID("eeeeeeee-1111-2222-3333-444455556666")

ACTIVE_ID = TEST_ID


def set_active(user_id):
    global ACTIVE_ID
    ACTIVE_ID = user_id


USER_TABLE = user_data_table_name(TEST_ID)
QUOTA_USER_TABLE = user_data_table_name(TEST_ID_QUOTA)

ALL_USER_IDS = [TEST_ID, TEST_ID_QUOTA, TEST_ID_NO_DATA, TEST_ID_OTHER]

# The narrative the (mocked) pipeline LLM returns.
PIPELINE_JSON = {
    "answer": "Revenue averaged 175.0 across the 2 periods in the data.",
    "visuals": [
        {
            "visual_type": "metric",
            "props": {"label": "Average daily revenue", "value": 175.0},
            "title": "Average revenue",
        }
    ],
    "insights": ["A possible contributing factor is the launch window."],
    "summary": "Reasonable growth across the window.",
    "root_causes": ["Correlates with the promotional week."],
    "recommendations": ["Consider pacing future promotions."],
    "news_context": [],
    "anomalies": [],
    "confidence": 0.8,
    "clarification": None,
}

HAPPY_SQL = f"SELECT created_at, revenue, region FROM {USER_TABLE} LIMIT 100"


@pytest.fixture(scope="module")
def db_engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("b4") / "app.db"
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
def seed(db_engine):
    """Reset the DB and seed users / uploads / data tables fresh per test."""
    maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _seed():
        async with maker() as s:
            await s.execute(text("DELETE FROM query_logs"))
            await s.execute(text("DELETE FROM file_uploads"))
            await s.execute(text("DELETE FROM users"))
            for uid in ALL_USER_IDS:
                await s.execute(
                    text(f'DROP TABLE IF EXISTS "{user_data_table_name(uid)}"')
                )
            await s.commit()

            def add_user(uid, email):
                s.add(
                    User(
                        id=uid,
                        email=email,
                        auth_provider="email",
                        plan="free",
                        is_active=True,
                        is_verified=True,
                    )
                )

            async def add_data(uid, table):
                await s.execute(
                    text(
                        f'CREATE TABLE "{table}" '
                        "(id INTEGER PRIMARY KEY, created_at DATETIME, "
                        "revenue INTEGER, region TEXT)"
                    )
                )
                await s.execute(
                    text(
                        f'INSERT INTO "{table}" (id, created_at, revenue, region) '
                        'VALUES (1, "2024-01-01", 100, "east"), '
                        '(2, "2024-01-02", 250, "west")'
                    )
                )
                s.add(
                    FileUpload(
                        id=uuid.uuid4(),
                        user_id=uid,
                        file_name="sales.csv",
                        file_type="text/csv",
                        file_size=10,
                        status="completed",
                        pinecone_namespace=table,
                    )
                )

            add_user(TEST_ID, "a@example.com")
            await add_data(TEST_ID, USER_TABLE)
            add_user(TEST_ID_QUOTA, "d@example.com")
            await add_data(TEST_ID_QUOTA, QUOTA_USER_TABLE)
            add_user(TEST_ID_NO_DATA, "c@example.com")
            add_user(TEST_ID_OTHER, "b@example.com")
            # a QueryLogs row owned by OTHER, used by the flag-404 test
            s.add(
                QueryLogs(
                    id=OTHER_LOG_ID,
                    user_id=TEST_ID_OTHER,
                    query="how's it going?",
                    response="{}",
                )
            )
            await s.commit()

    asyncio.run(_seed())
    return maker


@pytest.fixture()
def client(db_engine):
    maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    async def override_current_user(db: AsyncSession = Depends(get_db)):
        result = await db.execute(select(User).where(User.id == ACTIVE_ID))
        return result.scalar_one()

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _sql_fake(content):
    async def fake(prompt, system_prompt, temperature=0.3, max_tokens=512):
        return {"content": content, "source": "groq", "usage": None}

    return fake


def _pipeline_fake(payload):
    async def fake(prompt, system_prompt, temperature=0.3, max_tokens=512):
        return {"content": json.dumps(payload), "source": "groq", "usage": None}

    return fake


def mock_llms(monkeypatch, sql_content=HAPPY_SQL, pipeline_json=PIPELINE_JSON):
    monkeypatch.setattr("app.routes.chat.generate_response", _sql_fake(sql_content))
    monkeypatch.setattr(
        "app.services.llm.langchain_pipeline.generate_response",
        _pipeline_fake(pipeline_json),
    )


class TestChatHappyPath:
    def test_chat_returns_pipeline_output_with_traceability(self, client, seed, monkeypatch):
        mock_llms(monkeypatch)
        resp = client.post(
            "/chat", json={"query": "What is the average revenue?", "source_scope": "own_data"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"]
        assert body["visuals"][0]["visual_type"] == "metric"
        assert 0.0 <= body["confidence"] <= 1.0
        assert USER_TABLE in body["sql_query"]
        assert body["data_preview"] == [
            {"created_at": "2024-01-01", "revenue": 100, "region": "east"},
            {"created_at": "2024-01-02", "revenue": 250, "region": "west"},
        ]
        assert body["query_log_id"]

    def test_query_log_written_per_chat(self, client, seed, monkeypatch):
        mock_llms(monkeypatch)
        body = client.post("/chat", json={"query": "avg revenue?"}).json()
        log_id = uuid.UUID(body["query_log_id"])

        async def check():
            async with seed() as s:
                row = (
                    await s.execute(select(QueryLogs).where(QueryLogs.id == log_id))
                ).scalar_one()
                return row

        row = asyncio.run(check())
        assert row.query == "avg revenue?"
        assert "average" in row.response.lower() or "revenue" in row.response.lower()
        assert row.flagged is False
        assert row.user_id == TEST_ID

    def test_chat_defaults_to_own_data(self, client, seed, monkeypatch):
        mock_llms(monkeypatch)
        body = client.post("/chat", json={"query": "q"}).json()
        assert body["sql_query"]


class TestGracefulFallbacks:
    def test_invalid_query_sentinel_returns_graceful_message(self, client, seed, monkeypatch):
        mock_llms(monkeypatch, sql_content="SELECT 'INVALID_QUERY' LIMIT 1")
        resp = client.post("/chat", json={"query": "q"})
        assert resp.status_code == 200
        body = resp.json()
        assert "couldn't turn that into a query" in body["answer"]
        assert body["confidence"] == 0.0
        assert body["query_log_id"]

    def test_live_web_scope_is_gracefully_unsupported(self, client, seed):
        resp = client.post("/chat", json={"query": "q", "source_scope": "live_web"})
        assert resp.status_code == 200
        assert "Live web" in resp.json()["answer"]

    def test_no_data_returns_graceful_message(self, client, seed):
        set_active(TEST_ID_NO_DATA)
        try:
            resp = client.post("/chat", json={"query": "q"})
        finally:
            set_active(TEST_ID)
        assert resp.status_code == 200
        assert "haven't uploaded" in resp.json()["answer"]

    def test_clarification_mode_flows_through_route(self, client, seed, monkeypatch):
        clar = {"question": "Which quarter did you mean?", "options": ["Q1", "Q2", "Q3"]}
        payload = {
            **PIPELINE_JSON,
            "answer": "",
            "insights": [],
            "summary": "",
            "root_causes": [],
            "recommendations": [],
            "confidence": 0.0,
            "clarification": clar,
        }
        mock_llms(monkeypatch, pipeline_json=payload)
        resp = client.post("/chat", json={"query": "how did the quarter go?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["clarification"]["question"] == "Which quarter did you mean?"
        assert body["clarification"]["options"] == ["Q1", "Q2", "Q3"]
        assert body["answer"] == ""


class TestFlagEndpoint:
    def test_flag_own_answer_lands_on_query_log(self, client, seed, monkeypatch):
        mock_llms(monkeypatch)
        log_id = uuid.UUID(client.post("/chat", json={"query": "q"}).json()["query_log_id"])
        resp = client.post("/chat/flag", json={"query_log_id": str(log_id)})
        assert resp.status_code == 200
        assert resp.json() == {"query_log_id": str(log_id), "flagged": True}

        async def check():
            async with seed() as s:
                row = (
                    await s.execute(select(QueryLogs).where(QueryLogs.id == log_id))
                ).scalar_one()
                return row.flagged

        assert asyncio.run(check()) is True

    def test_flag_other_users_log_is_404(self, client, seed):
        resp = client.post("/chat/flag", json={"query_log_id": str(OTHER_LOG_ID)})
        assert resp.status_code == 404


class TestQuota:
    def test_window_exhausted_returns_429(self, client, seed, monkeypatch):
        set_active(TEST_ID_QUOTA)
        try:
            quota_sql = (
                f"SELECT created_at, revenue, region FROM {QUOTA_USER_TABLE} LIMIT 100"
            )
            mock_llms(monkeypatch, sql_content=quota_sql)
            for _ in range(4):
                resp = client.post("/chat", json={"query": "q"})
                assert resp.status_code == 200
            resp = client.post("/chat", json={"query": "q"})
            assert resp.status_code == 429
            assert "window" in resp.json()["detail"]
        finally:
            set_active(TEST_ID)