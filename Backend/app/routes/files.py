"""File upload + listing routes (Phase B3, specs/04).

`POST /files/upload` runs the existing validation dependency (non-guest, plan-based
size caps, extension + MIME double-check, 0-byte rejection) and returns 202 with a
FileResponse even when ingestion fails - a row is always created so the status
transition (`processing -> completed | failed`) is visible and never stuck.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.file_upload import FileUpload
from app.db.models.user import User
from app.middlewares.auth_middleware import get_current_user
from app.middlewares.file_validator import validate_file_upload
from app.schemas.file_upload import FileResponse
from app.services.data import parser, storage

router = APIRouter(prefix="/files", tags=["Files"])


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
        table_name = await parser.ingest_file(db, user.id, file.filename, contents)
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