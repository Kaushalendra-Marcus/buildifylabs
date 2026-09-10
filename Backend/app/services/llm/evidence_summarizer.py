"""Map-reduce compression for one oversized retrieved source (specs/07
hardening). Only triggers above app.config.Settings.SUMMARIZE_TRIGGER_CHARS
-- short sources are returned untouched with zero extra latency/cost, the
same behavior as today.
"""
import logging
from typing import Optional

from app.services.llm.groq_service import generate_response
from app.config import get_settings

logger = logging.getLogger(__name__)

SUMMARIZE_CHUNK_CHARS = 3000
# Bounds latency/cost: a 4-chunk cap covers ~12,000 chars of source content
# in at most 4 LLM calls, running concurrently (caller's responsibility).
SUMMARIZE_MAX_CHUNKS = 4

_CHUNK_SYSTEM_PROMPT = (
    "You compress source text for a research pipeline. You do NOT answer "
    "the user's question. Given one chunk of a longer source and the "
    "user's question for context, extract ONLY the facts, numbers, names, "
    "and dates in this chunk that are relevant to the question. Never "
    "invent anything not present in the chunk. Never add opinion or "
    "commentary. Return plain text, at most 120 words. If nothing in this "
    "chunk is relevant, return exactly: NOTHING_RELEVANT."
)


def _chunk_text(text: str, chunk_chars: int, max_chunks: int) -> list[str]:
    text = text or ""
    chunks = [text[i : i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    return chunks[:max_chunks]


async def summarize_long_text(
    text: str,
    query: str,
    *,
    chunk_chars: int = SUMMARIZE_CHUNK_CHARS,
    max_chunks: int = SUMMARIZE_MAX_CHUNKS,
) -> str:
    """Map-reduce compression of one long source. Splits into at most
    max_chunks chunks, summarizes each (fast model, temperature 0, strict
    no-invention prompt) focused on `query`, then joins the non-empty
    summaries. Short inputs at or under one chunk are returned untouched
    with zero LLM calls (cost-control). On ANY failure (LLM error, empty
    completions, etc.) returns the original text truncated to
    chunk_chars * max_chunks -- exactly today's EXTRACT_MAX_CHARS-style
    behavior, so this can only ever be a strict improvement over the
    status quo, never a regression.
    """
    settings = get_settings()
    fallback = text[: chunk_chars * max_chunks]
    try:
        if len(text or "") <= chunk_chars:
            return text
        chunks = _chunk_text(text, chunk_chars, max_chunks)
        if not chunks:
            return fallback
        import asyncio

        async def _summarize_chunk(chunk: str) -> Optional[str]:
            try:
                result = await generate_response(
                    prompt=f"User's question (context only):\n{query}\n\n"
                    f"Source chunk:\n{chunk}",
                    system_prompt=_CHUNK_SYSTEM_PROMPT,
                    model=settings.groq_fast_model,
                    temperature=0.0,
                    max_tokens=250,
                )
                content = (result.get("content") or "").strip()
                if not content or content == "NOTHING_RELEVANT":
                    return None
                return content
            except Exception as exc:
                logger.warning("Chunk summarization failed, skipping chunk: %s", exc)
                return None

        summaries = await asyncio.gather(*(_summarize_chunk(c) for c in chunks))
        kept = [s for s in summaries if s]
        if not kept:
            return fallback
        return " ".join(kept)
    except Exception as exc:
        logger.warning("Long-source summarization failed, using truncation: %s", exc)
        return fallback
