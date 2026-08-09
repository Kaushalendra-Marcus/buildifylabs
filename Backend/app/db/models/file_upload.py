from sqlalchemy import Column, UUID, DateTime, String, ForeignKey, Integer
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.database import Base
import uuid


class FileUpload(Base):
    __tablename__ = "file_uploads"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    file_name = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)
    file_size = Column(Integer, nullable=True)
    # The "storage reference" the ingestion pipeline sets alongside
    # status="completed" (specs/04 FR4, planning master B3). Holds the per-user
    # data-table name (user_data_table_name) the SQL layer queries until the
    # Pinecone namespace ships; then this column holds the actual namespace.
    pinecone_namespace = Column(String(255), nullable=True)
    status = Column(String(50), default="processing")
    # Failure reason when status transitions to "failed" (specs/04 edge case 1),
    # so an upload never sits stuck on "processing" with no explanation.
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="files")
