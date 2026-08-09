import logging

import sqlglot
from sqlglot import exp
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.middlewares.sql_sanitizer import sanitize_sql, SQL_DIALECT
from app.services.llm.sql_generator import is_invalid_query

logger = logging.getLogger(__name__)


class InvalidQueryError(Exception):
    """The model returned the INVALID_QUERY sentinel instead of a real query.

    The pipeline must short-circuit on this and show a user-facing "couldn't
    turn that into a query" message (specs/05 §5.3) - never execute it and
    return the string as if it were real data.
    """


def user_data_table_name(user_id) -> str:
    """Deterministic per-user data table name (co-designed with B3 storage).

    B3 creates this table when it ingests a file; the SQL layer (B2) executes
    against it. Because the table is unique to the user and only ever contains
    that user's rows, user-scoping is *structural* - a query against this
    table can't read another user's data even if the WHERE clause is wrong.
    """
    user_id = str(user_id)
    if "-" in user_id:
        user_id = user_id.replace("-", "")
    return f"user_{user_id}_data"


def assert_user_scoped(query: str, user_table: str) -> None:
    """Reject any query that references a table outside the caller's namespace.

    Post-generation validation (the second half of the specs/05 §5.5 fix): the
    AST walk allows only the user's own data table (optionally qualified as
    `public.<table>`), plus any CTE names the query itself defines. Anything
    else - a shared app table (`users`, `payments`, `query_logs`), another
    user's table, or a foreign schema - is a 403. This is tenant isolation,
    distinct from sanitize_sql()'s structural safety checks.
    """
    try:
        ast = sqlglot.parse_one(query, read=SQL_DIALECT)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse SQL query")

    cte_names = {
        node.alias.lower()
        for node in ast.walk()
        if isinstance(node, exp.CTE) and node.alias
    }

    user_table = user_table.lower()
    for node in ast.walk():
        if not isinstance(node, exp.Table):
            continue
        name = (node.name or "").lower()
        schema = (node.db or "").lower()
        if schema and schema not in ("public",):
            raise HTTPException(
                status_code=403,
                detail="Queries may only reference your own data",
            )
        if name in cte_names:
            continue
        if name != user_table:
            raise HTTPException(
                status_code=403,
                detail="Queries may only reference your own data",
            )


async def get_table_columns(db: AsyncSession, table_name: str) -> list[str]:
    """Introspect a table's columns (cross-dialect) for the dynamic SQL schema.

    B4's /chat calls this to feed the user's *real* uploaded columns into
    `build_data_schema()` / the SQL prompt, removing the placeholder
    `sales/customers/orders` fallback (specs/05 §4). Postgres uses
    `information_schema.columns`; SQLite (tests/dev) falls back to
    `PRAGMA table_info`.
    """
    table_name = table_name.lower()
    try:
        result = await db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t ORDER BY ordinal_position"
            ),
            {"t": table_name},
        )
        columns = [row["column_name"] for row in result.mappings()]
        if columns:
            return columns
    except Exception:
        pass

    result = await db.execute(text(f'PRAGMA table_info("{table_name}")'))
    return [row["name"] for row in result.mappings()]


async def execute_sql(query: str, db: AsyncSession, user_table: str) -> list[dict]:
    """Run a sanitized, user-scoped SELECT and return rows as a list of dicts.

    - INVALID_QUERY sentinel short-circuits (never executed, never returned as
      data) with InvalidQueryError for the caller to turn into a message.
    - sanitize_sql() re-gates the query (single read-only SELECT, LIMIT
      required, <=1000 chars) so nothing unsafe reaches the database.
    - assert_user_scoped() guarantees the query only touches the caller's table.
    - Empty result sets are a normal outcome -> [].
    - Postgres execution errors (hallucinated columns/tables) become a clean
      422, never a 500.
    """
    if is_invalid_query(query):
        raise InvalidQueryError("The question couldn't be turned into a query for your data.")

    sanitize_sql(query)
    assert_user_scoped(query, user_table)

    try:
        result = await db.execute(text(query))
    except SQLAlchemyError as exc:
        logger.warning(f"SQL execution failed: {exc}")
        await db.rollback()
        raise HTTPException(
            status_code=422,
            detail=(
                "Couldn't run the query against your data - it may reference "
                "a column or table that doesn't exist. Try rephrasing the question."
            ),
        )

    rows = result.mappings().all()
    return [dict(row) for row in rows]
