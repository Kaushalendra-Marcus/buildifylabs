"""vector_store orchestration (Part C). The AsyncSession is faked — no
Postgres needed. Retrieval is keyword overlap over stored chunk rows; the
fake filters rows by the user_id bound in the SQL, mirroring the WHERE
clause the real query carries."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import sqlite

import app.services.data.vector_store as vector_store_mod
from app.services.data.vector_store import (
    retrieve_document_evidence,
    search_chunks,
    store_chunks,
)


def run(coro):
    return asyncio.run(coro)


USER_ID = uuid.uuid4()
OTHER_ID = uuid.uuid4()
FILE_ID = uuid.uuid4()
FILE_ID_B = uuid.uuid4()


def _compiled_sql(statement):
    return str(
        statement.compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class _Result:
    def __init__(self, mappings=None, scalar=None):
        self._mappings = mappings or []
        self._scalar = scalar

    def mappings(self):
        return self._mappings

    def scalar_one_or_none(self):
        return self._scalar


def _chunk(user_id, content, index=0, file_id=FILE_ID):
    return {
        "content": content,
        "file_name": "report.pdf",
        "chunk_index": index,
        "created_at": "2026-09-01T00:00:00",
        "user_id": user_id,
        "file_id": file_id,
    }


def _db_with_chunks(chunks, viewer=USER_ID, file_ids=None):
    """Fake session whose filtering mirrors the real WHERE user_id (+ file_id)
    clauses. It also asserts the caller's ids actually reached the SQL."""

    def _ids_in(sql, ids):
        flat = sql.replace("-", "")
        return all(str(i).replace("-", "") in flat for i in ids)

    async def execute(statement, *args, **kwargs):
        sql = _compiled_sql(statement).replace("-", "")
        assert str(viewer).replace("-", "") in sql, "user_id must be bound in the SQL itself"
        picked = file_ids if file_ids else None
        if picked:
            assert _ids_in(sql, picked), "picked file_ids must be bound in the SQL itself"
        mine = [
            c for c in chunks
            if c["user_id"] == viewer and (picked is None or c["file_id"] in picked)
        ]
        if "document_chunks.id" in sql:
            return _Result(scalar=object() if mine else None)
        rows = [dict(c) for c in mine]
        return _Result(mappings=rows)

    return SimpleNamespace(execute=execute)


class TestStoreChunks:
    def test_stores_one_row_per_chunk(self):
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
        assert [row.content for row in added] == ["chunk one", "chunk two"]
        assert all(row.file_name == "report.pdf" for row in added)

    def test_no_chunks_stores_nothing(self):
        db = SimpleNamespace(add=lambda row: None, commit=AsyncMock())
        assert run(store_chunks(db, USER_ID, FILE_ID, "r.pdf", [])) == 0


class TestSearchChunks:
    def test_keyword_match_ranks_first(self):
        db = _db_with_chunks([
            _chunk(USER_ID, "The weather was mild.", index=0),
            _chunk(USER_ID, "Revenue was five million.", index=1),
        ])
        rows = run(search_chunks(db, USER_ID, "revenue?", top_k=2))
        assert [r["content"] for r in rows] == ["Revenue was five million."]

    def test_no_match_returns_empty(self):
        db = _db_with_chunks([_chunk(USER_ID, "Revenue was five million.")])
        assert run(search_chunks(db, USER_ID, "penguins", top_k=2)) == []

    def test_short_tokens_only_returns_empty(self):
        db = _db_with_chunks([_chunk(USER_ID, "Revenue was five million.")])
        assert run(search_chunks(db, USER_ID, "is a?", top_k=2)) == []

    def test_tenant_isolation(self):
        chunks = [
            _chunk(USER_ID, "Revenue was five million."),
            _chunk(OTHER_ID, "Revenue was nine million."),
        ]
        mine = run(search_chunks(_db_with_chunks(chunks, viewer=USER_ID), USER_ID, "revenue", top_k=5))
        assert [r["content"] for r in mine] == ["Revenue was five million."]
        theirs = run(
            search_chunks(_db_with_chunks(chunks, viewer=OTHER_ID), OTHER_ID, "revenue", top_k=5)
        )
        assert [r["content"] for r in theirs] == ["Revenue was nine million."]


class TestRetrieveDocumentEvidence:
    def test_no_chunks_returns_empty_without_search(self, monkeypatch):
        async def no_search(db, user_id, query_text, top_k):
            raise AssertionError("must not search when the user has no chunks")

        monkeypatch.setattr(vector_store_mod, "search_chunks", no_search)
        db = _db_with_chunks([], viewer=USER_ID)
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
        db = _db_with_chunks([_chunk(USER_ID, "Revenue was five million.")])
        assert run(retrieve_document_evidence(db, USER_ID, "q?")) == ([], [])

    def test_happy_path_returns_texts_and_tagged_sources(self):
        db = _db_with_chunks([_chunk(USER_ID, "Revenue was five million.")])
        texts, sources = run(retrieve_document_evidence(db, USER_ID, "revenue?"))
        assert texts == ["Revenue was five million."]
        assert sources[0]["provider"] == "your_documents"
        assert sources[0]["title"] == "report.pdf"

    def test_keyword_miss_falls_back_to_first_chunks(self):
        db = _db_with_chunks([
            _chunk(USER_ID, "Revenue was five million.", index=0),
            _chunk(USER_ID, "Costs held steady.", index=1),
        ])
        texts, sources = run(retrieve_document_evidence(db, USER_ID, "penguins"))
        assert texts == ["Revenue was five million.", "Costs held steady."]
        assert all(s["provider"] == "your_documents" for s in sources)

    def test_file_ids_restrict_to_picked_files(self):
        chunks = [
            _chunk(USER_ID, "Revenue was five million.", index=0, file_id=FILE_ID),
            _chunk(USER_ID, "Penguins migrate south.", index=0, file_id=FILE_ID_B),
        ]
        db = _db_with_chunks(chunks, viewer=USER_ID, file_ids=[FILE_ID_B])
        texts, sources = run(
            retrieve_document_evidence(db, USER_ID, "revenue?", [FILE_ID_B])
        )
        assert texts == ["Penguins migrate south."]
        assert sources[0]["title"] == "report.pdf"

    def test_file_ids_with_no_match_returns_empty(self):
        chunks = [_chunk(USER_ID, "Revenue was five million.", file_id=FILE_ID)]
        db = _db_with_chunks(chunks, viewer=USER_ID, file_ids=[FILE_ID_B])
        assert run(retrieve_document_evidence(db, USER_ID, "revenue?", [FILE_ID_B])) == ([], [])
