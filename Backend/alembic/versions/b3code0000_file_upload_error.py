"""add error column to file_uploads

Phase B3 (specs/04 FR4, edge case 1): FileUpload needs somewhere to store the
failure reason when the ingestion pipeline transitions status to "failed", so
a failed upload isn't stuck on "processing" with no explanation.

Revision ID: b3code0000
Revises: b1code0000
Create Date: 2026-08-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3code0000"
down_revision: Union[str, Sequence[str], None] = "b1code0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "file_uploads",
        sa.Column("error", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("file_uploads", "error")