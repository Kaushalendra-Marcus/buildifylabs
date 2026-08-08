"""quota rework: rolling 6h window + lifetime cap

Replaces the old calendar-day daily-tier fields (queries_today / last_reset)
with the rolling-window + lifetime-cap pair described in specs/02 (phase B1).

Revision ID: b1code0000
Revises: 9eec775a77e0
Create Date: 2026-08-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1code0000"
down_revision: Union[str, Sequence[str], None] = "9eec775a77e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "questions_in_window",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("questions_lifetime", sa.Integer(), server_default="0", nullable=False),
    )
    # NULL = window never started; the rollover rule treats it as "roll now".
    op.add_column(
        "users",
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("users", "queries_today")
    op.drop_column("users", "last_reset")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "users",
        sa.Column("last_reset", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.add_column("users", sa.Column("queries_today", sa.Integer(), nullable=True))
    op.drop_column("users", "window_started_at")
    op.drop_column("users", "questions_lifetime")
    op.drop_column("users", "questions_in_window")