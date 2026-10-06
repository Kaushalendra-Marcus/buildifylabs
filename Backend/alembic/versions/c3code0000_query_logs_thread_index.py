"""suggest (user_id, thread_id) index for per-thread prior context

Thread isolation (P0#20): `_load_prior_context` (app/routes/chat.py) scopes
prior-turn history by (user_id, thread_id), but `thread_id` currently lives
inside the QueryLogs.response JSON (`research_state.thread_id`), so the
lookup scans the user's recent rows in Python instead of seeking an index.

SUGGESTION (not yet applied — no schema change in this revision): once
`thread_id` is promoted to a real column, create the composite index so the
per-thread lookup stays O(log n) as history grows:

    ALTER TABLE query_logs ADD COLUMN thread_id TEXT;
    -- backfill from existing rows:
    --   UPDATE query_logs SET thread_id = response::json->'research_state'->>'thread_id';
    CREATE INDEX ix_query_logs_user_thread
        ON query_logs (user_id, thread_id);

Until then this migration is intentionally a no-op: applying an index over a
JSON-extracted expression would couple the migration to a Postgres-specific
expression index and risk breaking the SQLite test path, for zero behavior
gain while per-user history is small.

Revision ID: c3code0000
Revises: c2code0000
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "c3code0000"
down_revision: Union[str, Sequence[str], None] = "c2code0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (intentionally a no-op — see module docstring)."""


def downgrade() -> None:
    """Downgrade schema (intentionally a no-op — see module docstring)."""
