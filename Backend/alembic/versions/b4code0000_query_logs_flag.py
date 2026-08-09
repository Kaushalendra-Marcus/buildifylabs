"""add flagged column to query_logs

Phase B4 (specs/10 §2): the "flag this answer" mechanism needs a place to
land - QueryLogs.flagged is set via POST /chat/flag so a wrong/misleading
answer surfaces somewhere reviewed rather than a black hole.

Revision ID: b4code0000
Revises: b3code0000
Create Date: 2026-08-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b4code0000"
down_revision: Union[str, Sequence[str], None] = "b3code0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "query_logs",
        sa.Column(
            "flagged",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("query_logs", "flagged")