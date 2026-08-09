from fastapi import Depends, HTTPException, UploadFile, status
from app.db.models.user import User

from app.middlewares.auth_middleware import get_current_user
import os
import logging

logger = logging.getLogger(__name__)

LIMITS = {
    "free": 3 * 1024 * 1024,
    "pro": 10 * 1024 * 1024,
}

# Allowed file types, keyed by extension. The spec's double-check (specs/04 §4)
# is enforced *per type*: the extension must be known AND the declared
# content_type must match that extension's MIME exactly (e.g. `.csv` with
# `application/pdf` is a mismatched pair and is rejected by design).
EXT_TO_MIME = {
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


async def validate_file_upload(
    file: UploadFile,
    user: User = Depends(get_current_user),
):
    if user.auth_provider == "guest":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest users cannot upload files. Please sign up.",
        )

    limit = LIMITS.get(user.plan)

    if limit is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid plan",
        )

    ext = os.path.splitext(file.filename)[1].lower()
    expected_mime = EXT_TO_MIME.get(ext)
    if expected_mime is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Invalid file type. Only CSV, PDF and Excel files are allowed.",
        )

    # Strip any media-type parameters ("text/csv; charset=utf-8") before comparing.
    declared_type = (file.content_type or "").split(";")[0].strip().lower()
    if declared_type != expected_mime:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The file's extension and content type don't match.",
        )

    contents = await file.read()
    file_size = len(contents)

    await file.seek(0)

    # specs/04 edge case 4: an empty (0-byte) file passes the size check
    # trivially and must be rejected explicitly, not silently ingested as an
    # empty dataset. Checked the moment bytes are read, before any persistence.
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty (0 bytes). Upload a non-empty CSV, PDF or Excel file.",
        )

    if file_size > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File too large. Max allowed: {limit // (1024*1024)} MB",
        )

    return file
