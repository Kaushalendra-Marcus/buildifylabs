"""PDF text extraction + chunking for document QA (Part C).

Pure functions, no DB/network — `document_ingest` (via `parser.ingest_file`)
and `vector_store` orchestrate storage.
Fails loud on genuinely unreadable PDFs (caller/route already catches and
sets FileUpload.status="failed", matching the existing CSV/XLSX contract);
degrades gracefully on partial extraction (some pages readable, some not)
by skipping unreadable pages rather than failing the whole file.
"""
import io
import logging
from typing import List

from pypdf import PdfReader

from app.config import get_settings

logger = logging.getLogger(__name__)


def extract_pdf_text(contents: bytes) -> str:
    """Extract running text from every readable page, capped by settings.

    Scanned/image-only PDFs yield little or no text — this is a known
    limitation (OCR is out of scope), surfaced honestly: a PDF that
    extracts to near-empty text should fail ingestion with a clear reason,
    not silently create zero chunks.
    """
    settings = get_settings()
    reader = PdfReader(io.BytesIO(contents))
    parts: List[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning(f"Skipping unreadable PDF page: {exc}")
            continue
        if not text.strip():
            continue
        parts.append(text)
        total += len(text)
        if total >= settings.MAX_DOCUMENT_CHARS:
            break
    return "\n\n".join(parts)[: settings.MAX_DOCUMENT_CHARS]


def chunk_text(text: str) -> List[str]:
    """Fixed-size character chunking with overlap (simple, deterministic —
    no sentence/paragraph-boundary detection needed for v1; overlap covers
    the case where a fact straddles a chunk boundary)."""
    settings = get_settings()
    size = settings.DOCUMENT_CHUNK_CHARS
    overlap = settings.DOCUMENT_CHUNK_OVERLAP_CHARS
    text = (text or "").strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(text) and len(chunks) < settings.MAX_CHUNKS_PER_FILE:
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start <= 0:
            break
    return chunks
