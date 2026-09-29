"""Document chunk storage + keyword retrieval (Part C). Postgres in
production; the retrieval query is plain SQLAlchemy so it also runs on
SQLite in tests.

PDF text lands here as plain-text chunks, retrieved by keyword overlap
scoped to user_id at query time. No embedding service is involved on any
path: upload and retrieval are fully local, so a network or vendor outage
can never fail a file or blank a document answer.
"""
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.document_chunk import DocumentChunk

logger = logging.getLogger(__name__)

DOCUMENT_SOURCE_PROVIDER = "your_documents"  # matches the frontend literal

# Candidate cap per retrieval: bounds the read while staying far above any
# realistic per-user chunk count at current 3 MB upload limits.
MAX_CANDIDATE_CHUNKS = 500

_WORD_RE = re.compile(r"[a-z0-9]+")


def _query_tokens(query_text: str) -> List[str]:
    """Lowercase alphanumeric tokens of length >= 3. Short tokens ("is",
    "of", "Q1") match everywhere and only add noise to the ranking."""
    return [w for w in _WORD_RE.findall(query_text.lower()) if len(w) >= 3]


async def store_chunks(
    db: AsyncSession, user_id, file_id, file_name: str, chunks: List[str],
) -> int:
    """Persist every chunk as-is. Returns the count stored. Pure local DB
    work — nothing here can fail on network, so a stored file stays stored."""
    if not chunks:
        return 0
    for index, chunk in enumerate(chunks):
        db.add(DocumentChunk(
            id=uuid.uuid4(), user_id=user_id, file_id=file_id, file_name=file_name,
            chunk_index=index, content=chunk,
        ))
    await db.commit()
    return len(chunks)


async def delete_file_chunks(db: AsyncSession, user_id, file_id) -> None:
    await db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.user_id == user_id, DocumentChunk.file_id == file_id,
        )
    )
    await db.commit()


async def search_chunks(
    db: AsyncSession, user_id, query_text: str, top_k: int,
) -> List[dict]:
    """Keyword-overlap top-K, scoped to user_id at the SQL level (never
    filtered after the fact — same tenant-isolation discipline as
    executor.py::assert_user_scoped). Ranked by total query-token hits,
    ties broken by chunk order so results are deterministic."""
    tokens = _query_tokens(query_text)
    if not tokens:
        return []
    result = await db.execute(
        select(
            DocumentChunk.content,
            DocumentChunk.file_name,
            DocumentChunk.chunk_index,
            DocumentChunk.created_at,
        )
        .where(DocumentChunk.user_id == user_id)
        .order_by(DocumentChunk.chunk_index)
        .limit(MAX_CANDIDATE_CHUNKS)
    )
    scored = []
    for row in result.mappings():
        haystack = (row["content"] or "").lower()
        score = sum(haystack.count(tok) for tok in tokens)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1]["chunk_index"]))
    return [
        {
            "content": row["content"],
            "file_name": row["file_name"],
            "chunk_index": row["chunk_index"],
            "created_at": row["created_at"],
            "score": score,
        }
        for score, row in scored[:top_k]
    ]


async def user_has_document_chunks(db: AsyncSession, user_id) -> bool:
    result = await db.execute(
        select(DocumentChunk.id).where(DocumentChunk.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def retrieve_document_evidence(
    db: AsyncSession, user_id, query_text: str,
) -> Tuple[List[str], List[dict]]:
    """Orchestration entry point chat.py calls. Fails soft end to end: any
    DB failure returns ([], []) and is logged, never raised — document
    evidence is a bonus channel, not a required one. Returns (texts,
    source_dicts) in the exact `news_context`/`web_sources` shape so chat.py
    can merge them with zero new PipelineOutput fields."""
    settings = get_settings()
    if not settings.ENABLE_DOCUMENT_QA:
        return [], []
    try:
        if not await user_has_document_chunks(db, user_id):
            return [], []
        rows = await search_chunks(
            db, user_id, query_text, settings.MAX_DOCUMENT_CHUNKS_PER_QUERY,
        )
    except Exception as exc:
        logger.warning(f"Document retrieval skipped (fail-soft): {exc}")
        return [], []
    texts = [row["content"] for row in rows]
    sources = [
        {
            "title": row["file_name"],
            "url": "",
            "provider": DOCUMENT_SOURCE_PROVIDER,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "published_date": str(row["created_at"])[:10] if row.get("created_at") else None,
            "score": row.get("score"),
        }
        for row in rows
    ]
    return texts, sources
