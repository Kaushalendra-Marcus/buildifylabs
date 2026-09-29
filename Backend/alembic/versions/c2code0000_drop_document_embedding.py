"""drop document_chunks.embedding (keyword retrieval needs no vectors)

Document QA no longer uses an embedding service: chunks are stored as
plain text and retrieved by keyword overlap, so the pgvector column and
its ivfflat index serve no purpose. The table, its FKs, and the pgvector
extension itself are left untouched.

Revision ID: c2code0000
Revises: c1code0000
Create Date: 2026-09-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "c2code0000"
down_revision: Union[str, Sequence[str], None] = "c1code0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding")
    op.drop_column("document_chunks", "embedding")


def downgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("embedding", Vector(384), nullable=True),
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
