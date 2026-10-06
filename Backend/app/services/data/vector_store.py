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
from typing import List, Optional, Tuple

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

# Summarize-style queries carry no keywords to match ("summarize my PDF",
# "what do you know about my data"): the opening chunks ARE the answer, so
# they keep the first_chunks fallback. Any other query with tokens but zero
# hits returns [] so the judge can clarify instead of answering off-topic.
_SUMMARIZE_RE = re.compile(
    r"summar(y|ies|ize|ise|izing|ising)|overview|tldr\b|"
    r"what\s+do\s+you\s+know|about\s+my\s+(data|document|file|pdf|report)|"
    r"\bdescribe\b.*(data|document|file)|"
    r"\blist\b.*(file|document)|"
    r"\bshow\b.*(file|document)",
    re.IGNORECASE,
)


def _query_tokens(query_text: str) -> List[str]:
    """Lowercase alphanumeric tokens of length >= 3. Short tokens ("is",
    "of", "Q1") match everywhere and only add noise to the ranking."""
    return [w for w in _WORD_RE.findall(query_text.lower()) if len(w) >= 3]


def _is_summarize_query(query_text: str) -> bool:
    """True when the query asks for an overview rather than keywords."""
    try:
        return bool(_SUMMARIZE_RE.search(str(query_text or "")))
    except Exception:
        return False


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


def _scope_filter(user_id, file_ids: Optional[list] = None):
    """Tenant filter shared by every chunk read: this user's rows, and only
    the picked files when the chat document picker sent a selection."""
    filt = [DocumentChunk.user_id == user_id]
    if file_ids:
        filt.append(DocumentChunk.file_id.in_(list(file_ids)))
    return filt


async def search_chunks(
    db: AsyncSession, user_id, query_text: str, top_k: int,
    file_ids: Optional[list] = None,
) -> List[dict]:
    """Keyword-overlap top-K, scoped to user_id at the SQL level (never
    filtered after the fact — same tenant-isolation discipline as
    executor.py::assert_user_scoped). When file_ids is given, only the
    picked uploads are searched. Ranked by total query-token hits,
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
        .where(*_scope_filter(user_id, file_ids))
        .order_by(DocumentChunk.chunk_index)
        .limit(MAX_CANDIDATE_CHUNKS)
    )
    scored = []
    for row in result.mappings():
        # Word-boundary matching: exact token hits, never substring counts
        # ("car" must not match "scar"/"oscar"; "art" must not match
        # "heart"/"cart"). Score is the total whole-word hit count.
        haystack_tokens = _WORD_RE.findall((row["content"] or "").lower())
        counts: dict = {}
        for word in haystack_tokens:
            counts[word] = counts.get(word, 0) + 1
        score = sum(counts.get(tok, 0) for tok in tokens)
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


async def user_has_document_chunks(
    db: AsyncSession, user_id, file_ids: Optional[list] = None
) -> bool:
    result = await db.execute(
        select(DocumentChunk.id)
        .where(*_scope_filter(user_id, file_ids))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def first_chunks(
    db: AsyncSession, user_id, top_k: int, file_ids: Optional[list] = None
) -> List[dict]:
    """First top_k chunks in document order, tenant-scoped (and picker-
    scoped when file_ids is given). The fallback for queries that share no
    keywords with the text (e.g. "summarize my PDF", "what do you know
    about my data"): vector search used to always return nearest chunks
    even at low similarity, so returning the opening chunks keeps that
    contract — the judge still decides relevance."""
    result = await db.execute(
        select(
            DocumentChunk.content,
            DocumentChunk.file_name,
            DocumentChunk.chunk_index,
            DocumentChunk.created_at,
        )
        .where(*_scope_filter(user_id, file_ids))
        .order_by(DocumentChunk.chunk_index)
        .limit(top_k)
    )
    return [
        {
            "content": row["content"],
            "file_name": row["file_name"],
            "chunk_index": row["chunk_index"],
            "created_at": row["created_at"],
            "score": None,
        }
        for row in result.mappings()
    ]


async def retrieve_document_evidence(
    db: AsyncSession, user_id, query_text: str,
    file_ids: Optional[list] = None,
) -> Tuple[List[str], List[dict]]:
    """Orchestration entry point chat.py calls. Fails soft end to end: any
    DB failure returns ([], []) and is logged, never raised — document
    evidence is a bonus channel, not a required one. Returns (texts,
    source_dicts) in the exact `news_context`/`web_sources` shape so chat.py
    can merge them with zero new PipelineOutput fields. When file_ids is
    given, scoping is strict: only the picked uploads contribute, and an
    empty pick match returns empty (never silently answers from unselected
    files)."""
    settings = get_settings()
    if not settings.ENABLE_DOCUMENT_QA:
        return [], []
    try:
        if not await user_has_document_chunks(db, user_id, file_ids):
            return [], []
        rows = await search_chunks(
            db, user_id, query_text, settings.MAX_DOCUMENT_CHUNKS_PER_QUERY,
            file_ids,
        )
        if not rows:
            # Zero hits with real query tokens -> [] so the judge can
            # clarify (never answer off-topic from unrelated opening
            # chunks). Only summarize-style queries keep the opening-chunks
            # fallback: they share no keywords by construction.
            if _is_summarize_query(query_text):
                rows = await first_chunks(
                    db, user_id, settings.MAX_DOCUMENT_CHUNKS_PER_QUERY,
                    file_ids,
                )
            else:
                return [], []
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
