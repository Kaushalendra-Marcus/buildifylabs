from sqlalchemy import Column, DateTime, String, ForeignKey, Text, UUID, Numeric, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.database import Base
import uuid


class QueryLogs(Base):
    __tablename__ = "query_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    query = Column(String, nullable=False)
    response = Column(Text, nullable=True)
    intent = Column(String(100), nullable=True)
    execution_time = Column(Numeric(6, 3), nullable=True)
    # specs/10 §2 flag mechanism (Phase B4): set via POST /chat/flag when a user
    # marks an answer as wrong/misleading, so it lands somewhere reviewed
    # instead of a black hole.
    flagged = Column(Boolean, default=False, server_default="false", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="queries")
