"""Persist the durable raw-upload object reference."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5code0000"
down_revision: Union[str, Sequence[str], None] = "c4code0000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("file_uploads", sa.Column("storage_key", sa.String(length=512)))


def downgrade() -> None:
    op.drop_column("file_uploads", "storage_key")
