from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional


class FileResponse(BaseModel):
    id: UUID
    file_name: str
    file_type: str
    file_size: Optional[int] = None
    status: str
    pinecone_namespace: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class FilePreview(BaseModel):
    """First rows of a dataset for the Data page preview drawer.

    `kind` is "table" (CSV/XLSX — columns/rows from the per-user data table)
    or "documents" (PDF — the first stored chunks, no embedding needed).
    """

    file_id: UUID
    file_name: str
    kind: str
    columns: list[str]
    rows: list[dict]
