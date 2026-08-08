from sqlalchemy import Column, Integer, String, Boolean, DateTime, UUID
from sqlalchemy.sql import func
from app.db.database import Base
from sqlalchemy.orm import relationship
import uuid


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, index=True, unique=True, nullable=True)
    name = Column(String, nullable=True)
    hashed_password = Column(String, nullable=True)  # "email" / "google" / "guest"
    auth_provider = Column(String, nullable=False)
    google_id = Column(String, unique=True, nullable=True)
    # Rolling-window + lifetime quota (specs/02, phase B1). Replaces the old
    # calendar-day daily-tier fields (queries_today / last_reset) which the
    # rewrite removed.
    questions_in_window = Column(Integer, default=0, server_default="0")
    window_started_at = Column(DateTime(timezone=True), nullable=True)
    questions_lifetime = Column(Integer, default=0, server_default="0")
    is_active = Column(Boolean, default=True)
    plan = Column(String, default="free")
    device_fingerprint = Column(String(255), unique=True, nullable=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    queries = relationship("QueryLogs", back_populates="user")
    # Must be named "files" - FileUpload.user declares
    # back_populates="files", and a mismatched name here makes SQLAlchemy
    # raise an ArgumentError as soon as mappers are configured (i.e. the
    # app can't boot at all).
    files = relationship("FileUpload", back_populates="user")
    payments = relationship("Payment", back_populates="user")
