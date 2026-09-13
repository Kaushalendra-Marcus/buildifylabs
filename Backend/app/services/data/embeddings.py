"""HF Inference API embeddings (Part C). Mirrors groq_service.py's
hf_fallback() shape: httpx call, fail-soft, one clear error type."""
import logging
from typing import List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when the embedding call fails outright — callers decide
    whether that means "fail this file's ingestion" (ingestion time) or
    "skip document evidence for this query" (query time, fail soft)."""


def _hf_url(model: str) -> str:
    return f"https://api-inference.huggingface.co/pipeline/feature-extraction/{model}"


async def embed_texts(texts: List[str]) -> List[Optional[List[float]]]:
    """Embed a batch of strings. Returns one vector per input, same order;
    an individual failed item is None (caller skips that chunk), a total
    request failure raises EmbeddingError (caller decides fail-file vs
    fail-soft per the docstring above)."""
    settings = get_settings()
    if not texts:
        return []
    url = _hf_url(settings.DOCUMENT_EMBEDDING_MODEL)
    headers = {"Authorization": f"Bearer {settings.HF_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url, headers=headers,
                json={"inputs": texts, "options": {"wait_for_model": True}},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning(f"Embedding request failed: {exc}")
        raise EmbeddingError(str(exc)) from exc
    if not isinstance(data, list):
        raise EmbeddingError(f"Unexpected embedding response shape: {type(data)}")
    out: List[Optional[List[float]]] = []
    for item in data:
        if isinstance(item, list) and item and isinstance(item[0], (int, float)):
            out.append([float(v) for v in item])
        elif isinstance(item, list):
            # Some deployments return per-token vectors; mean-pool to one.
            try:
                dim = len(item[0])
                pooled = [sum(tok[i] for tok in item) / len(item) for i in range(dim)]
                out.append(pooled)
            except Exception:
                out.append(None)
        else:
            out.append(None)
    while len(out) < len(texts):
        out.append(None)
    return out


async def embed_text(text: str) -> Optional[List[float]]:
    """Single-string convenience wrapper (query-time embedding)."""
    result = await embed_texts([text])
    return result[0] if result else None
