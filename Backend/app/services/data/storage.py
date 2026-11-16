"""Storage backend for uploaded files (Phase B3 — resolves gap #4).

Dev/prod decision (planning master §Backend risks #1): **local disk for dev**,
object store (S3) for prod. This module is the single seam — swap these two
functions for an object-store client without touching the routes or parser.

Only the raw uploaded bytes are persisted here; the *parsed* data lives in the
per-user data table created by `app/services/data/parser.py`.
"""
from pathlib import Path

from app.config import get_settings


def save_raw_file(user_id, file_id, original_filename: str, content: bytes) -> str:
    """Persist one upload's raw bytes under UPLOAD_DIR/<user_id>/<file_id>.

    The on-disk name is the upload's UUID (plus original extension), never the
    caller-supplied filename, so a crafted `filename` can't escape the user's
    directory or collide; the original name stays in the FileUpload row.
    """
    settings = get_settings()
    if getattr(settings, "STORAGE_BACKEND", "local") == "s3":
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("S3 storage is configured but boto3 is not installed") from exc
        key = f"{user_id}/{file_id}{Path(original_filename).suffix.lower()}"
        client = boto3.client(
            "s3",
            endpoint_url=getattr(settings, "S3_ENDPOINT_URL", None),
            region_name=getattr(settings, "S3_REGION", "auto"),
            aws_access_key_id=getattr(settings, "S3_ACCESS_KEY_ID", None),
            aws_secret_access_key=getattr(settings, "S3_SECRET_ACCESS_KEY", None),
        )
        bucket = getattr(settings, "S3_BUCKET", None)
        if not bucket:
            raise RuntimeError("S3 storage requires S3_BUCKET")
        client.put_object(Bucket=bucket, Key=key, Body=content)
        return f"s3://{bucket}/{key}"

    directory = Path(settings.UPLOAD_DIR) / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    ext = Path(original_filename).suffix.lower()
    path = directory / f"{file_id}{ext}"
    path.write_bytes(content)
    return str(path)


def delete_raw_file(user_id, file_id) -> bool:
    """Remove one upload's raw bytes (best-effort, for DELETE /files/{id}).

    Matches `<file_id>.*` inside the user's directory (the on-disk name is
    the upload UUID plus its original extension). Missing files and unlink
    errors are swallowed — the DB row is the source of truth, never the disk.
    Returns True if at least one file was removed.
    """
    settings = get_settings()
    if getattr(settings, "STORAGE_BACKEND", "local") == "s3":
        try:
            import boto3
        except ImportError:
            return False
        client = boto3.client(
            "s3",
            endpoint_url=getattr(settings, "S3_ENDPOINT_URL", None),
            region_name=getattr(settings, "S3_REGION", "auto"),
            aws_access_key_id=getattr(settings, "S3_ACCESS_KEY_ID", None),
            aws_secret_access_key=getattr(settings, "S3_SECRET_ACCESS_KEY", None),
        )
        bucket = getattr(settings, "S3_BUCKET", None)
        if not bucket:
            return False
        prefix = f"{user_id}/{file_id}"
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
        if not objects:
            return False
        client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
        return True

    directory = Path(settings.UPLOAD_DIR) / str(user_id)
    removed = False
    try:
        candidates = list(directory.glob(f"{file_id}.*"))
    except OSError:
        return False
    for path in candidates:
        try:
            path.unlink()
            removed = True
        except OSError:
            continue
    return removed