from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, UUID, DateTime, String, ForeignKey, Integer, Text
from sqlalchemy.sql import func
from app.db.database import Base
import uuid


class DocumentChunk(Base):
    """One embedded chunk of an uploaded PDF (Part C). No ORM relationship
    on User/FileUpload — vector_store.py queries this table directly by
    user_id/file_id, matching the plain-FK style QueryLogs already uses."""
    __tablename__ = "document_chunks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("file_uploads.id"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
