"""add document_chunks table + pgvector extension

Part C (document QA): PDF text lands here as embedded chunks, retrieved by
cosine similarity scoped to user_id at query time. XLSX/CSV keep using the
per-user SQL table unchanged — this is additive, not a replacement.

Revision ID: c1code0000
Revises: b4code0000
Create Date: 2026-09-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "c1code0000"
down_revision: Union[str, Sequence[str], None] = "b4code0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("file_id", sa.UUID(as_uuid=True), sa.ForeignKey("file_uploads.id"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_document_chunks_user_id", "document_chunks", ["user_id"])
    op.create_index("ix_document_chunks_file_id", "document_chunks", ["file_id"])
    # ivfflat needs rows to train on well; at MVP scale a plain scan is fine,
    # but create the index now so it's not a forgotten follow-up at scale:
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.drop_index("ix_document_chunks_file_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_user_id", table_name="document_chunks")
    op.drop_table("document_chunks")
