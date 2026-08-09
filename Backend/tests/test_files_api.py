"""End-to-end routes for /files (Phase B3, specs/04 §3 contracts + §6).

Uses FastAPI's TestClient with dependency overrides (fake user + a real
file-backed SQLite DB) so uploads persist across requests within a test, and
the raw file lands in a temp UPLOAD_DIR. Covers the acceptance criteria:
guest 403, 15MB pro 413, .exe->csv 415, 0-byte 400, and a processed CSV
being queryable via its per-user table.
"""

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
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
from app.middlewares.auth_middleware import get_current_user  # noqa: E402
from app.services.data import storage as storage_module  # noqa: E402
from app.services.data.executor import user_data_table_name  # noqa: E402

TEST_ID_A = uuid.UUID("aaaaaaaa-1111-2222-3333-444455556666")
TEST_ID_B = uuid.UUID("bbbbbbbb-1111-2222-3333-444455556666")

_FREE_USER = SimpleNamespace(id=TEST_ID_A, plan="free", auth_provider="email")
_PRO_USER = SimpleNamespace(id=TEST_ID_A, plan="pro", auth_provider="email")
_GUEST_USER = SimpleNamespace(id=TEST_ID_A, plan="free", auth_provider="guest")
_OTHER_USER = SimpleNamespace(id=TEST_ID_B, plan="free", auth_provider="email")

ACTIVE_USER = _FREE_USER


def set_active(user):
    global ACTIVE_USER
    ACTIVE_USER = user


@pytest.fixture(scope="module")
def db_engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("b3") / "app.db"
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
def uploads_dir(tmp_path):
    return tmp_path / "uploads"


@pytest.fixture()
def client(db_engine, uploads_dir, monkeypatch):
    maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    def override_current_user():
        return ACTIVE_USER

    monkeypatch.setattr(
        storage_module,
        "get_settings",
        lambda: SimpleNamespace(UPLOAD_DIR=str(uploads_dir)),
    )

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def do_upload(client, filename, content, content_type):
    return client.post(
        "/files/upload",
        files={"file": (filename, content, content_type)},
    )


class TestUploadRejections:
    def test_guest_403(self, client):
        set_active(_GUEST_USER)
        try:
            resp = do_upload(client, "sales.csv", b"a,b\n1,2\n", "text/csv")
        finally:
            set_active(_FREE_USER)
        assert resp.status_code == 403

    def test_zero_byte_400(self, client):
        resp = do_upload(client, "empty.csv", b"", "text/csv")
        assert resp.status_code == 400

    def test_wrong_extension_415(self, client):
        resp = do_upload(client, "evil.exe", b"a,b\n", "text/csv")
        assert resp.status_code == 415

    def test_mime_mismatch_415(self, client):
        resp = do_upload(client, "data.csv", b"a,b\n", "application/pdf")
        assert resp.status_code == 415

    def test_free_15mb_413(self, client):
        resp = do_upload(client, "big.csv", b"x" * (15 * 1024 * 1024), "text/csv")
        assert resp.status_code == 413

    def test_pro_15mb_413(self, client):
        set_active(_PRO_USER)
        try:
            resp = do_upload(
                client, "bigpro.csv", b"x" * (15 * 1024 * 1024), "text/csv"
            )
        finally:
            set_active(_FREE_USER)
        assert resp.status_code == 413


class TestHappyPath:
    CSV = b"date,revenue,region\n2024-01-01,100,east\n2024-01-02,250,west\n"

    def test_valid_csv_returns_202_completed_body(self, client, uploads_dir):
        resp = do_upload(client, "sales.csv", self.CSV, "text/csv")
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "completed"
        assert body["file_name"] == "sales.csv"
        assert body["file_type"] == "text/csv"
        assert body["file_size"] == len(self.CSV)
        assert body["pinecone_namespace"] == user_data_table_name(TEST_ID_A)

        saved = list((uploads_dir / str(TEST_ID_A)).glob("*.csv"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == self.CSV

    def test_processed_csv_queryable_via_per_user_table(self, client, db_engine):
        do_upload(client, "q.csv", self.CSV, "text/csv")

        async def check():
            maker = async_sessionmaker(db_engine, expire_on_commit=False)
            async with maker() as session:
                result = await session.execute(
                    text(
                        "SELECT revenue FROM "
                        f'"{user_data_table_name(TEST_ID_A)}" ORDER BY revenue'
                    )
                )
                return [r["revenue"] for r in result.mappings()]

        assert asyncio.run(check()) == [100, 250]

    def test_list_and_get_own_files(self, client):
        before = len(client.get("/files").json())
        do_upload(client, "a.csv", b"a,b\n1,2\n", "text/csv")
        do_upload(client, "b.csv", b"a,b\n3,4\n", "text/csv")

        listing = client.get("/files")
        assert listing.status_code == 200
        files = listing.json()
        assert len(files) == before + 2

        one = client.get(f"/files/{files[0]['id']}")
        assert one.status_code == 200
        assert one.json()["id"] == files[0]["id"]
        assert {"a.csv", "b.csv"} <= {f["file_name"] for f in files}

    def test_get_other_users_file_is_404(self, client):
        mine = do_upload(client, "a.csv", b"a,b\n1,2\n", "text/csv").json()["id"]

        set_active(_OTHER_USER)
        try:
            other = client.get(f"/files/{mine}")
        finally:
            set_active(_FREE_USER)
        assert other.status_code == 404

    def test_uploaded_xlsx_fails_with_stored_reason(self, client):
        resp = do_upload(
            client,
            "data.xlsx",
            b"PK\x03\x04somebinary",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "failed"
        assert "not supported yet" in body["error"]

    def test_uploaded_pdf_fails_with_stored_reason(self, client):
        resp = do_upload(client, "report.pdf", b"%PDF-1.4fake", "application/pdf")
        assert resp.status_code == 202
        assert resp.json()["status"] == "failed"