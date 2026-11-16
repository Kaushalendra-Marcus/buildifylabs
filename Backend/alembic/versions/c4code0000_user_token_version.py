"""Revoke rotated refresh tokens and password-reset sessions.

Revision ID: c4code0000
Revises: c3code0000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4code0000"
down_revision: Union[str, Sequence[str], None] = "c3code0000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
