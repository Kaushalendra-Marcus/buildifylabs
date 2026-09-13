"""pgvector integration (Part C): the real `<=>` cosine query, tenant
isolation, and nearest-neighbor ordering against a real Postgres.

GATED: runs only when TEST_POSTGRES_URL is set, e.g.::

    TEST_POSTGRES_URL=postgresql+asyncpg://user:pass@host/db \\
        python -m pytest tests/test_vector_store_integration.py

Point it at a scratch Neon branch (or local Postgres) with
`CREATE EXTENSION vector;` already run. Every other test in the suite is
unaffected and stays fast/dependency-free.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="needs a real Postgres with pgvector (set TEST_POSTGRES_URL)",
)

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.db.database import Base  # noqa: E402
from app.services.data.vector_store import (  # noqa: E402
    search_chunks,
    store_chunks,
    user_has_document_chunks,
)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


async def _fresh_db():
    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


def test_search_chunks_returns_nearest_first_and_isolates_tenants(monkeypatch):
    import app.services.data.vector_store as vector_store_mod

    async def scenario():
        engine = await _fresh_db()
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            user_a, user_b = uuid.uuid4(), uuid.uuid4()
            file_a = uuid.uuid4()

            async def fake_embed(texts):
                # "alpha" chunks point along +x, "beta" along +y.
                out = []
                for text in texts:
                    out.append([1.0, 0.0] if "alpha" in text else [0.0, 1.0])
                return out

            monkeypatch.setattr(vector_store_mod, "embed_texts", fake_embed)
            async with maker() as session:
                stored = await store_chunks(
                    session, user_a, file_a, "a.pdf", ["alpha one", "beta two"]
                )
                assert stored == 2
                assert await user_has_document_chunks(session, user_a) is True
                assert await user_has_document_chunks(session, user_b) is False

                rows = await search_chunks(session, user_a, [1.0, 0.0], top_k=2)
                assert [row["content"] for row in rows] == ["alpha one", "beta two"]

                # Tenant isolation at the SQL level: user_b sees nothing.
                assert await search_chunks(session, user_b, [1.0, 0.0], top_k=2) == []
        finally:
            await engine.dispose()

    _run(scenario())
