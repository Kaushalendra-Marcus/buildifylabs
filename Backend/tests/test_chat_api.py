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
    async def fake(prompt, system_prompt, temperature=0.3, max_tokens=512, **kwargs):
        return {"content": content, "source": "groq", "usage": None}

    return fake


def _pipeline_fake(payload):
    async def fake(prompt, system_prompt, temperature=0.3, max_tokens=512, **kwargs):
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

    def test_live_web_scope_uses_retrieved_web_context(self, client, seed, monkeypatch):
        mock_llms(monkeypatch)
        monkeypatch.setattr(
            "app.routes.chat.search_web",
            lambda query, company_name=None, prior_clarification=None, **kwargs: asyncio.sleep(0, result=["Live result for q"]),
        )
        resp = client.post("/chat", json={"query": "q", "source_scope": "live_web"})
        assert resp.status_code == 200
        assert resp.json()["answer"]

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

    def test_clarification_with_null_options_flows_through_route(self, client, seed, monkeypatch):
        # The model sometimes emits options: null (seen live with Groq). That
        # must coerce to [] and still render as a clarification — never a
        # generic fallback.
        clar = {"question": "Which AI business should I compare?", "options": None}
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
        resp = client.post("/chat", json={"query": "which ai business is best?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["clarification"]["question"] == "Which AI business should I compare?"
        assert body["clarification"]["options"] == []
        assert body["answer"] == ""

    def test_answer_without_visuals_gets_guaranteed_table(self, client, seed, monkeypatch):
        # The narrator returns a naked answer over real rows: the guarantee
        # must table/chart it from the seeded sales rows, values traceable.
        payload = {**PIPELINE_JSON, "visuals": []}
        mock_llms(monkeypatch, pipeline_json=payload)
        resp = client.post("/chat", json={"query": "how is revenue?"})
        assert resp.status_code == 200
        body = resp.json()
        kinds = [visual["visual_type"] for visual in body["visuals"]]
        assert "table" in kinds and "graph" in kinds
        table = next(v for v in body["visuals"] if v["visual_type"] == "table")
        flat = json.dumps(table["props"]["values"])
        assert "100" in flat and "250" in flat
        graph = next(v for v in body["visuals"] if v["visual_type"] == "graph")
        assert graph["props"]["chart_type"] == "bar"
        assert graph["props"]["labels"] == ["east", "west"]

    def test_followup_chart_uses_prior_answer_data(self, client, seed, monkeypatch):
        # Turn 1 logs an answer with a data preview; turn 2 ("show in chart
        # form", live web so no fresh rows) must resolve from the prior rows
        # instead of clarifying.
        mock_llms(monkeypatch, pipeline_json={**PIPELINE_JSON, "visuals": []})
        first = client.post("/chat", json={"query": "how is revenue?"})
        assert first.status_code == 200
        assert first.json()["data_preview"]

        monkeypatch.setattr(
            "app.routes.chat.search_web",
            lambda query, company_name=None, prior_clarification=None, **kwargs: asyncio.sleep(0, result=["Live result for q"]),
        )
        second = client.post(
            "/chat", json={"query": "show in chart form", "source_scope": "live_web"}
        )
        assert second.status_code == 200
        body = second.json()
        assert body["clarification"] is None
        kinds = [visual["visual_type"] for visual in body["visuals"]]
        assert "graph" in kinds
        graph = next(v for v in body["visuals"] if v["visual_type"] == "graph")
        assert graph["props"]["labels"] == ["east", "west"]

    def test_live_web_answer_carries_thinking_sources_and_citations(
        self, client, seed, monkeypatch
    ):
        from types import SimpleNamespace

        # A qualitative web answer: citations kept iff they point at listed
        # snippets, a sources table synthesized, and a thinking trace present.
        mock_llms(
            monkeypatch,
            pipeline_json={
                **PIPELINE_JSON,
                "answer": "Acme raised $50M [1] while others surged [7].",
                "visuals": [],
            },
        )
        monkeypatch.setattr(
            "app.routes.chat.search_web",
            lambda query, company_name=None, prior_clarification=None, **kwargs: asyncio.sleep(
                0,
                result=SimpleNamespace(
                    context=["Acme raised $50M in 2024", "Globex launched Y"],
                    sources=[
                        {
                            "title": "Acme funding",
                            "url": "https://a.example",
                            "provider": "DuckDuckGo",
                            "retrieved_at": "x",
                        },
                        {
                            "title": "Globex launch",
                            "url": "https://b.example",
                            "provider": "DuckDuckGo",
                            "retrieved_at": "x",
                        },
                    ],
                    market_data=[],
                ),
            ),
        )
        resp = client.post(
            "/chat", json={"query": "startup funding news", "source_scope": "live_web"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "[1]" in body["answer"]
        assert "[7]" not in body["answer"]
        tables = [v for v in body["visuals"] if v["visual_type"] == "table"]
        # Figures only: no duplicate "Sources cited" table — sources render
        # once in the expandable section below the answer.
        assert [table["title"] for table in tables] == ["Figures cited"]
        assert tables[0]["props"]["columns"] == ["Figure", "Context"]
        assert body["thinking"]
        assert any("Judged" in step for step in body["thinking"])
        assert len(body["web_sources"]) == 2
        # No followups requested by the mock narration -> empty, not null.
        assert body["followups"] == []
        # Every listed source carries a title + provider for its citation.
        assert all(
            {"title", "provider"} <= set(source) for source in body["web_sources"]
        )


class TestChatStream:
    def test_stream_emits_stages_then_result(self, client, seed, monkeypatch):
        import json as jsonlib

        mock_llms(monkeypatch, pipeline_json={**PIPELINE_JSON, "visuals": []})
        resp = client.post("/chat/stream", json={"query": "how is revenue?"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = [
            jsonlib.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        stages = [event["stage"] for event in events if "stage" in event]
        assert stages[:2] == ["evidence", "judging"]
        assert "narrating" in stages and "visuals" in stages

        results = [event["result"] for event in events if "result" in event]
        assert len(results) == 1
        assert results[0]["answer"]
        assert results[0]["query_log_id"]
        kinds = [visual["visual_type"] for visual in results[0]["visuals"]]
        assert "table" in kinds

    def test_stream_emits_answer_text_before_result(self, client, seed, monkeypatch):
        import json as jsonlib

        # Narration streams its JSON in chunks: the client must see {"text"}
        # prose deltas first, then the identical prose inside {"result"}.
        full = jsonlib.dumps(PIPELINE_JSON)
        chunks = [full[:20], full[20:60], full[60:]]

        async def fake_stream(**kwargs):
            for chunk in chunks:
                yield chunk

        monkeypatch.setattr(
            "app.services.llm.langchain_pipeline.stream_response", fake_stream
        )
        monkeypatch.setattr(
            "app.routes.chat.generate_response", _sql_fake(HAPPY_SQL)
        )
        monkeypatch.setattr(
            "app.services.llm.langchain_pipeline.generate_response",
            _pipeline_fake(PIPELINE_JSON),
        )
        resp = client.post(
            "/chat/stream", json={"query": "What is the average revenue?"}
        )
        assert resp.status_code == 200

        events = [
            jsonlib.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        texts = [event["text"] for event in events if "text" in event]
        results = [event["result"] for event in events if "result" in event]
        assert len(texts) >= 2
        assert len(results) == 1
        # Post-narration grounding may append notes (exclusion disclosure),
        # so the streamed prose is a prefix of — never different from — the
        # final answer.
        assert results[0]["answer"].startswith("".join(texts))
        assert "".join(texts) == PIPELINE_JSON["answer"]


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
        # usage.WINDOW_QUESTIONS_LIMIT is raised for general testing; pin it
        # back to the spec's small window here so exhaustion is reachable.
        from app.utils import usage as usage_mod

        monkeypatch.setattr(usage_mod, "WINDOW_QUESTIONS_LIMIT", 4)
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


class TestExternalContextClarification:
    def test_own_data_scope_with_external_request_asks_clarification(
        self, client, seed, monkeypatch
    ):
        mock_llms(monkeypatch)
        resp = client.post(
            "/chat",
            json={
                "query": "check the news on fuel prices this month",
                "source_scope": "own_data",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["clarification"] is not None
        assert (
            body["clarification"]["question"]
            == "Want me to also check live sources for this one?"
        )
        assert body["clarification"]["options"] == [
            "Yes, check live sources too",
            "No, just my data",
        ]
        assert body["confidence"] == 0.0

    def test_own_data_scope_plain_question_does_not_clarify(
        self, client, seed, monkeypatch
    ):
        mock_llms(monkeypatch)
        resp = client.post(
            "/chat",
            json={"query": "What is the average revenue?", "source_scope": "own_data"},
        )
        assert resp.status_code == 200
        assert resp.json()["clarification"] is None

    def test_external_context_yes_reply_triggers_live_search(
        self, client, seed, monkeypatch
    ):
        mock_llms(monkeypatch)
        first = client.post(
            "/chat",
            json={
                "query": "check the news on fuel prices this month",
                "source_scope": "own_data",
            },
        )
        assert first.status_code == 200
        assert first.json()["clarification"] is not None

        called = {}

        async def fake_search(query, company_name=None, prior_clarification=None, **kwargs):
            called["yes"] = True
            return ["Live result for yes"]

        monkeypatch.setattr("app.routes.chat.search_web", fake_search)
        second = client.post(
            "/chat",
            json={"query": "Yes, check live sources too", "source_scope": "own_data"},
        )
        assert second.status_code == 200
        assert called.get("yes") is True


class TestSqlSelfCorrection:
    """Phase 3 (reliability hardening): one bounded SQL retry with the real
    error + real schema fed back, instead of an immediate rephrase ask."""

    BAD_SQL = f"SELECT nonexistent_column FROM {USER_TABLE} LIMIT 100"

    def test_sql_error_triggers_one_retry_and_succeeds(
        self, client, seed, monkeypatch
    ):
        sql_calls = {"n": 0}

        async def sql_fake(prompt, system_prompt, temperature=0.3, max_tokens=512, **kwargs):
            sql_calls["n"] += 1
            content = self.BAD_SQL if sql_calls["n"] == 1 else HAPPY_SQL
            return {"content": content, "source": "groq", "usage": None}

        monkeypatch.setattr("app.routes.chat.generate_response", sql_fake)
        monkeypatch.setattr(
            "app.services.llm.langchain_pipeline.generate_response",
            _pipeline_fake(PIPELINE_JSON),
        )
        resp = client.post(
            "/chat",
            json={"query": "What is the average revenue?", "source_scope": "own_data"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"]
        assert body["visuals"]
        assert USER_TABLE in body["sql_query"]
        assert "nonexistent_column" not in body["sql_query"]
        assert sql_calls["n"] == 2

    def test_sql_error_retry_also_fails_returns_honest_fallback(
        self, client, seed, monkeypatch
    ):
        sql_calls = {"n": 0}

        async def sql_fake(prompt, system_prompt, temperature=0.3, max_tokens=512, **kwargs):
            sql_calls["n"] += 1
            return {"content": self.BAD_SQL, "source": "groq", "usage": None}

        monkeypatch.setattr("app.routes.chat.generate_response", sql_fake)
        monkeypatch.setattr(
            "app.services.llm.langchain_pipeline.generate_response",
            _pipeline_fake(PIPELINE_JSON),
        )
        resp = client.post(
            "/chat",
            json={"query": "What is the average revenue?", "source_scope": "own_data"},
        )
        # The retry must not mask a genuinely bad question: today's exact
        # graceful fallback stands, and the retry stays bounded (no loop).
        assert resp.status_code == 200
        assert "Couldn't run the query" in resp.json()["answer"]
        assert sql_calls["n"] == 2

    def test_sql_success_on_first_try_never_triggers_retry(
        self, client, seed, monkeypatch
    ):
        sql_calls = {"n": 0}

        async def sql_fake(prompt, system_prompt, temperature=0.3, max_tokens=512, **kwargs):
            sql_calls["n"] += 1
            return {"content": HAPPY_SQL, "source": "groq", "usage": None}

        monkeypatch.setattr("app.routes.chat.generate_response", sql_fake)
        monkeypatch.setattr(
            "app.services.llm.langchain_pipeline.generate_response",
            _pipeline_fake(PIPELINE_JSON),
        )
        resp = client.post(
            "/chat",
            json={"query": "What is the average revenue?", "source_scope": "own_data"},
        )
        assert resp.status_code == 200
        assert resp.json()["answer"]
        assert sql_calls["n"] == 1