"""File upload + listing routes (Phase B3, specs/04).

`POST /files/upload` runs the existing validation dependency (non-guest, plan-based
size caps, extension + MIME double-check, 0-byte rejection) and returns 202 with a
FileResponse even when ingestion fails - a row is always created so the status
transition (`processing -> completed | failed`) is visible and never stuck.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.document_chunk import DocumentChunk
from app.db.models.file_upload import FileUpload
from app.db.models.user import User
from app.middlewares.auth_middleware import get_current_user
from app.middlewares.file_validator import validate_file_upload
from app.schemas.file_upload import FilePreview, FileResponse
from app.services.data import parser, storage
from app.services.data.executor import get_table_columns, user_data_table_name

router = APIRouter(prefix="/files", tags=["Files"])

# Preview cap: enough to recognize the dataset, never a full dump.
PREVIEW_MAX_ROWS = 10


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile = Depends(validate_file_upload),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    contents = await file.read()

    upload_id = uuid.uuid4()
    upload = FileUpload(
        id=upload_id,
        user_id=user.id,
        file_name=file.filename,
        file_type=file.content_type,
        file_size=len(contents),
        status="processing",
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    table_name = None
    try:
        storage.save_raw_file(user.id, upload_id, file.filename or "", contents)
        table_name = await parser.ingest_file(db, user.id, upload_id, file.filename, contents)
    except Exception as exc:
        # specs/04 edge case 1: never leave the row stuck on "processing".
        # Roll back whatever ingest did and record the failed status + reason.
        await db.rollback()
        failed = await db.get(FileUpload, upload_id)
        failed.status = "failed"
        failed.error = str(exc)[:500]
        await db.commit()
        await db.refresh(failed)
        return failed

    upload.status = "completed"
    # "storage reference" the plan B3 says to set with completed: the per-user
    # table the SQL layer queries (until Pinecone namespaces ship, this column
    # holds the table name).
    upload.pinecone_namespace = table_name
    await db.commit()
    await db.refresh(upload)
    return upload


@router.get("", response_model=list[FileResponse])
async def list_files(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FileUpload)
        .where(FileUpload.user_id == user.id)
        .order_by(FileUpload.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FileUpload).where(
            FileUpload.id == file_id, FileUpload.user_id == user.id
        )
    )
    upload = result.scalar_one_or_none()
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return upload


async def _own_upload_or_404(
    db: AsyncSession, user: User, file_id: uuid.UUID
) -> FileUpload:
    """Fetch one upload scoped to the caller — other users' ids are 404s,
    never 403s, so ids can't be probed (same rule as GET /files/{id})."""
    result = await db.execute(
        select(FileUpload).where(
            FileUpload.id == file_id, FileUpload.user_id == user.id
        )
    )
    upload = result.scalar_one_or_none()
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return upload


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete one upload and everything it landed: the row, the raw bytes,
    the per-user data table (only when this file is the active one — a newer
    upload replaces the table, and deleting an older row must not drop the
    current data), and the PDF chunks for document uploads."""
    if getattr(user, "auth_provider", None) == "guest":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest accounts cannot manage files",
        )
    upload = await _own_upload_or_404(db, user, file_id)

    namespace = upload.pinecone_namespace or ""
    if namespace == user_data_table_name(user.id) and upload.status == "completed":
        # The per-user table always mirrors the LATEST completed tabular
        # upload (one-file-at-a-time, specs/04 §4) — drop it only when the
        # deleted file is that owner. Deleting an older row is metadata-only.
        latest_id = await db.scalar(
            select(FileUpload.id)
            .where(
                FileUpload.user_id == user.id,
                FileUpload.status == "completed",
                FileUpload.pinecone_namespace == namespace,
            )
            .order_by(FileUpload.created_at.desc(), FileUpload.id.desc())
            .limit(1)
        )
        if latest_id == upload.id:
            await db.execute(text(f'DROP TABLE IF EXISTS "{namespace}"'))
    elif namespace.startswith("vector:"):
        await db.execute(
            delete(DocumentChunk).where(
                DocumentChunk.file_id == upload.id,
                DocumentChunk.user_id == user.id,
            )
        )

    storage.delete_raw_file(user.id, upload.id)
    await db.delete(upload)
    await db.commit()
    return None


@router.get("/{file_id}/preview", response_model=FilePreview)
async def preview_file(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """First rows of a dataset for the Data page preview drawer. Tabular
    files read the per-user data table; PDFs return the first stored chunks
    (a plain select — no embedding call). Anything without landed data
    (processing/failed rows, or a replaced table) is an honest 404."""
    upload = await _own_upload_or_404(db, user, file_id)
    namespace = upload.pinecone_namespace or ""

    if namespace == user_data_table_name(user.id):
        columns = await get_table_columns(db, namespace)
        # Hide the synthetic `id` PK upsert_user_table adds — the preview
        # shows the user's own columns only.
        columns = [c for c in columns if c != "id"]
        if not columns:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No preview available — the data table for this file is gone. Upload it again.",
            )
        result = await db.execute(
            text(f'SELECT * FROM "{namespace}" LIMIT :n'),
            {"n": PREVIEW_MAX_ROWS},
        )
        rows = [
            {k: v for k, v in dict(row).items() if k != "id"}
            for row in result.mappings().all()
        ]
        return FilePreview(
            file_id=upload.id,
            file_name=upload.file_name,
            kind="table",
            columns=columns,
            rows=rows,
        )

    if namespace.startswith("vector:"):
        result = await db.execute(
            select(DocumentChunk.chunk_index, DocumentChunk.content)
            .where(
                DocumentChunk.file_id == upload.id,
                DocumentChunk.user_id == user.id,
            )
            .order_by(DocumentChunk.chunk_index)
            .limit(PREVIEW_MAX_ROWS)
        )
        chunks = result.all()
        if not chunks:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No preview available — this file has no stored content.",
            )
        return FilePreview(
            file_id=upload.id,
            file_name=upload.file_name,
            kind="documents",
            columns=["chunk_index", "content"],
            rows=[
                {"chunk_index": index, "content": content}
                for index, content in chunks
            ],
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No preview available for this file yet.",
    )