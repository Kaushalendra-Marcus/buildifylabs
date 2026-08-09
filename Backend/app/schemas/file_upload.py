from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional


class FileUploadRequest(BaseModel):
    file_name: str
    file_type: str
    file_size: Optional[int] = None


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
