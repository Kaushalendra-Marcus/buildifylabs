"""vector_store orchestration (Part C). The embedding calls and the
AsyncSession are mocked — no Postgres needed. The real `<=>` SQL is covered
by the explicitly-gated tests/test_vector_store_integration.py instead."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.services.data.vector_store as vector_store_mod
from app.services.data.embeddings import EmbeddingError
from app.services.data.vector_store import (
    retrieve_document_evidence,
    store_chunks,
)


def run(coro):
    return asyncio.run(coro)


USER_ID = uuid.uuid4()
FILE_ID = uuid.uuid4()


class TestStoreChunks:
    def test_stores_one_row_per_embedded_chunk(self, monkeypatch):
        async def fake_embed(texts):
            return [[float(i)] * 4 for i, _ in enumerate(texts)]

        monkeypatch.setattr(vector_store_mod, "embed_texts", fake_embed)
        added = []
        db = SimpleNamespace(
            add=lambda row: added.append(row), commit=AsyncMock()
        )

        stored = run(
            store_chunks(db, USER_ID, FILE_ID, "report.pdf", ["chunk one", "chunk two"])
        )
        assert stored == 2
        assert len(added) == 2
        assert [row.chunk_index for row in added] == [0, 1]
        assert all(row.file_name == "report.pdf" for row in added)

    def test_none_vectors_skipped_not_fatal(self, monkeypatch):
        async def fake_embed(texts):
            return [[[0.1] * 4][0], None]

        monkeypatch.setattr(vector_store_mod, "embed_texts", fake_embed)
        added = []
        db = SimpleNamespace(
            add=lambda row: added.append(row), commit=AsyncMock()
        )

        stored = run(store_chunks(db, USER_ID, FILE_ID, "r.pdf", ["a", "b"]))
        assert stored == 1
        assert len(added) == 1

    def test_total_embedding_failure_raises(self, monkeypatch):
        async def fake_embed(texts):
            raise EmbeddingError("down")

        monkeypatch.setattr(vector_store_mod, "embed_texts", fake_embed)
        db = SimpleNamespace(add=lambda row: None, commit=AsyncMock())

        try:
            run(store_chunks(db, USER_ID, FILE_ID, "r.pdf", ["a"]))
        except EmbeddingError:
            return
        raise AssertionError("expected EmbeddingError")

    def test_no_chunks_stores_nothing(self, monkeypatch):
        db = SimpleNamespace(add=lambda row: None, commit=AsyncMock())
        assert run(store_chunks(db, USER_ID, FILE_ID, "r.pdf", [])) == 0


class TestRetrieveDocumentEvidence:
    def _db_with_rows(self, has_rows=True):
        has = AsyncMock()
        has.scalar_one_or_none = lambda: object() if has_rows else None

        async def execute(statement, *args, **kwargs):
            return has

        return SimpleNamespace(execute=execute)

    def test_no_chunks_returns_empty_without_embedding(self, monkeypatch):
        async def no_embed(text):
            raise AssertionError("must not embed when the user has no chunks")

        monkeypatch.setattr(vector_store_mod, "embed_text", no_embed)
        db = self._db_with_rows(has_rows=False)
        assert run(retrieve_document_evidence(db, USER_ID, "q?")) == ([], [])

    def test_kill_switch_skips_retrieval(self, monkeypatch):
        from types import SimpleNamespace as _NS

        monkeypatch.setattr(
            vector_store_mod,
            "get_settings",
            lambda: _NS(
                ENABLE_DOCUMENT_QA=False, MAX_DOCUMENT_CHUNKS_PER_QUERY=6
            ),
        )
        db = self._db_with_rows(has_rows=True)
        assert run(retrieve_document_evidence(db, USER_ID, "q?")) == ([], [])

    def test_embedding_failure_fails_soft(self, monkeypatch):
        async def fake_embed(text):
            raise EmbeddingError("down")

        monkeypatch.setattr(vector_store_mod, "embed_text", fake_embed)
        db = self._db_with_rows(has_rows=True)
        assert run(retrieve_document_evidence(db, USER_ID, "q?")) == ([], [])

    def test_happy_path_returns_texts_and_tagged_sources(self, monkeypatch):
        async def fake_embed(text):
            return [0.1] * 4

        async def fake_search(db, user_id, query_vector, top_k):
            assert top_k > 0
            return [
                {
                    "content": "Revenue was five million.",
                    "file_name": "report.pdf",
                    "chunk_index": 0,
                    "created_at": "2026-09-01T00:00:00",
                    "score": 0.9,
                }
            ]

        monkeypatch.setattr(vector_store_mod, "embed_text", fake_embed)
        monkeypatch.setattr(vector_store_mod, "search_chunks", fake_search)
        db = self._db_with_rows(has_rows=True)

        texts, sources = run(retrieve_document_evidence(db, USER_ID, "revenue?"))
        assert texts == ["Revenue was five million."]
        assert sources[0]["provider"] == "your_documents"
        assert sources[0]["title"] == "report.pdf"
