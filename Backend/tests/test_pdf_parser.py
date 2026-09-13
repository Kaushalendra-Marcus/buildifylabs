"""PDF text extraction + chunking (Part C, document QA).

The fixture PDF is generated at test time with fpdf2 (dev-only, guarded by
importorskip) so no binary fixture is checked in. The parser branch itself
is covered with a mocked store_chunks (no network/embeddings needed).
"""

import asyncio
import uuid

import pytest

from app.services.data.pdf_parser import chunk_text, extract_pdf_text


def _make_pdf_bytes(lines):
    fpdf = pytest.importorskip("fpdf")
    pdf = fpdf.FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.cell(0, 10, text=line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


class TestExtractPdfText:
    def test_recovers_known_text(self):
        content = _make_pdf_bytes(
            ["Annual revenue was five million.", "Costs held steady."]
        )
        text = extract_pdf_text(content)
        assert "five million" in text
        assert "Costs held steady" in text

    def test_multipage_text_joined(self):
        fpdf = pytest.importorskip("fpdf")
        pdf = fpdf.FPDF()
        for word in ("alpha-page", "beta-page"):
            pdf.add_page()
            pdf.set_font("Helvetica", size=12)
            pdf.cell(0, 10, text=word, new_x="LMARGIN", new_y="NEXT")
        text = extract_pdf_text(bytes(pdf.output()))
        assert "alpha-page" in text and "beta-page" in text

    def test_garbage_bytes_raise(self):
        with pytest.raises(Exception):
            extract_pdf_text(b"%PDF-1.4fake")


class TestChunkText:
    def test_short_text_is_one_chunk(self):
        assert chunk_text("hello world") == ["hello world"]

    def test_empty_text_is_no_chunks(self):
        assert chunk_text("   ") == []

    def test_long_text_chunks_overlap(self):
        from app.config import get_settings

        settings = get_settings()
        size, overlap = (
            settings.DOCUMENT_CHUNK_CHARS,
            settings.DOCUMENT_CHUNK_OVERLAP_CHARS,
        )
        text = "x" * (size * 2 + 10)
        chunks = chunk_text(text)
        assert len(chunks) == 3
        assert all(len(c) <= size for c in chunks)
        # Overlap: chunk 2 starts (size - overlap) into the text.
        assert chunks[1][:overlap] == text[size - overlap : size]


class TestPdfIngestBranch:
    def test_pdf_returns_vector_reference(self, monkeypatch):
        import app.services.data.vector_store as vector_store_mod
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.data.parser import ingest_file

        async def fake_store(db, user_id, file_id, file_name, chunks):
            assert len(chunks) >= 1
            assert file_name == "report.pdf"
            return len(chunks)

        monkeypatch.setattr(vector_store_mod, "store_chunks", fake_store)
        content = _make_pdf_bytes(["Quarterly report. Revenue grew."])

        async def go():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            try:
                maker = async_sessionmaker(engine, expire_on_commit=False)
                async with maker() as session:
                    upload_id = uuid.uuid4()
                    ref = await ingest_file(
                        session,
                        "30303030-1111-2222-3333-444455556666",
                        upload_id,
                        "report.pdf",
                        content,
                    )
                    assert ref == f"vector:{upload_id}"
            finally:
                await engine.dispose()

        asyncio.run(go())

    def test_unreadable_pdf_fails_with_clear_reason(self):
        pytest.importorskip("fpdf")
        from fpdf import FPDF

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.data.parser import ingest_file

        pdf = FPDF()
        pdf.add_page()
        content = bytes(pdf.output())
        assert extract_pdf_text(content).strip() == ""

        async def go():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            try:
                maker = async_sessionmaker(engine, expire_on_commit=False)
                async with maker() as session:
                    with pytest.raises(ValueError, match="No readable text"):
                        await ingest_file(
                            session,
                            "30303030-1111-2222-3333-444455556666",
                            uuid.uuid4(),
                            "scanned.pdf",
                            content,
                        )
            finally:
                await engine.dispose()

        asyncio.run(go())
