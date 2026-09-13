"""pgvector CRUD + retrieval (Part C). Postgres-only (the `<=>` cosine
operator and the Vector column type don't exist on SQLite) — see
tests/test_vector_store_integration.py for how the SQL itself is covered
without requiring every dev/CI run to have a real Postgres instance."""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.document_chunk import DocumentChunk
from app.services.data.embeddings import EmbeddingError, embed_text, embed_texts

logger = logging.getLogger(__name__)

DOCUMENT_SOURCE_PROVIDER = "your_documents"  # matches the frontend literal


async def store_chunks(
    db: AsyncSession, user_id, file_id, file_name: str, chunks: List[str],
) -> int:
    """Embed and store every chunk. Returns the count actually stored (a
    chunk whose embedding call failed is skipped, not fatal to the file —
    matches evidence_summarizer.py's per-item fail-soft philosophy)."""
    if not chunks:
        return 0
    try:
        vectors = await embed_texts(chunks)
    except EmbeddingError:
        # Ingestion-time: the whole file has no usable evidence -> fail it,
        # matching the existing FileUpload status="failed" contract.
        raise
    stored = 0
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        if vector is None:
            continue
        db.add(DocumentChunk(
            id=uuid.uuid4(), user_id=user_id, file_id=file_id, file_name=file_name,
            chunk_index=index, content=chunk, embedding=vector,
        ))
        stored += 1
    await db.commit()
    return stored


async def delete_file_chunks(db: AsyncSession, user_id, file_id) -> None:
    await db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.user_id == user_id, DocumentChunk.file_id == file_id,
        )
    )
    await db.commit()


async def search_chunks(
    db: AsyncSession, user_id, query_embedding: List[float], top_k: int,
) -> List[dict]:
    """Cosine-similarity top-K, scoped to user_id at the SQL level (never
    filtered after the fact — same tenant-isolation discipline as
    executor.py::assert_user_scoped). Postgres/pgvector only."""
    result = await db.execute(
        text(
            "SELECT content, file_name, chunk_index, created_at, "
            "1 - (embedding <=> :qvec) AS score "
            "FROM document_chunks WHERE user_id = :uid "
            "ORDER BY embedding <=> :qvec LIMIT :k"
        ),
        {"qvec": str(query_embedding), "uid": str(user_id), "k": top_k},
    )
    return [dict(row) for row in result.mappings()]


async def user_has_document_chunks(db: AsyncSession, user_id) -> bool:
    result = await db.execute(
        select(DocumentChunk.id).where(DocumentChunk.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def retrieve_document_evidence(
    db: AsyncSession, user_id, query_text: str,
) -> Tuple[List[str], List[dict]]:
    """Orchestration entry point chat.py calls. Fails soft end to end: any
    failure (embedding, DB) returns ([], []) and is logged, never raised —
    document evidence is a bonus channel, not a required one. Returns
    (texts, source_dicts) in the exact `news_context`/`web_sources` shape
    so chat.py can merge them with zero new PipelineOutput fields."""
    settings = get_settings()
    if not settings.ENABLE_DOCUMENT_QA:
        return [], []
    try:
        if not await user_has_document_chunks(db, user_id):
            return [], []
        query_vector = await embed_text(query_text)
        if query_vector is None:
            return [], []
        rows = await search_chunks(
            db, user_id, query_vector, settings.MAX_DOCUMENT_CHUNKS_PER_QUERY,
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
