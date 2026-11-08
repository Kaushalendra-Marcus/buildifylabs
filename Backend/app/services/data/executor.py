import logging
import re
from datetime import date, datetime
from decimal import Decimal

import sqlglot
from sqlglot import exp
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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

    await _apply_statement_timeout(db)

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


async def _apply_statement_timeout(db: AsyncSession) -> None:
    """Postgres only: cap how long a generated query may run.

    `SET LOCAL` lasts until the transaction ends, so it never leaks to other
    requests. Skipped silently on any other dialect (SQLite in tests) or if the
    session isn't a real async session.
    """
    try:
        dialect = getattr(getattr(getattr(db, "bind", None), "dialect", None), "name", None)
        if dialect != "postgresql":
            return
        millis = int(getattr(get_settings(), "SQL_STATEMENT_TIMEOUT_MS", 10000))
        if millis > 0:
            await db.execute(text(f"SET LOCAL statement_timeout = {millis}"))
    except Exception as exc:
        logger.warning(f"Statement timeout not applied: {exc}")


# ---------------------------------------------------------------------------
# Data profile: real facts about the user's table (types, ranges, top values,
# latest date). Fed to SQL generation (so the model stops guessing values and
# resolves "last week" against the data, not today's date) and to narration
# (so the answer can state what its evidence covers). Fail-soft: {} on error.
# ---------------------------------------------------------------------------

_PROFILE_MAX_COLUMNS = 40
_PROFILE_TOP_VALUE_COLUMNS = 6
_PROFILE_TOP_VALUES = 12
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_OTHER_TYPE_HINTS = ("json", "uuid", "bytea", "array", "[]", "interval", "point", "inet", "blob")
_NUMERIC_TYPE_HINTS = ("int", "numeric", "decimal", "double", "real", "float", "money", "number")


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _classify_type(raw_type: str) -> str:
    t = (raw_type or "").lower()
    if "bool" in t:
        return "bool"
    if any(hint in t for hint in _OTHER_TYPE_HINTS):
        return "other"
    if "date" in t or "time" in t:
        return "date"
    if any(hint in t for hint in _NUMERIC_TYPE_HINTS):
        return "numeric"
    return "text"


def _jsonable(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


async def _column_types(db: AsyncSession, table_name: str) -> dict:
    table_name = table_name.lower()
    try:
        result = await db.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t ORDER BY ordinal_position"
            ),
            {"t": table_name},
        )
        types = {row["column_name"]: str(row["data_type"] or "") for row in result.mappings()}
        if types:
            return types
    except Exception:
        pass
    try:
        result = await db.execute(text(f'PRAGMA table_info("{table_name}")'))
        return {row["name"]: str(row["type"] or "") for row in result.mappings()}
    except Exception:
        return {}


async def get_data_profile(db: AsyncSession, table_name: str, columns=None) -> dict:
    """Profile a per-user data table. Returns {} when anything goes wrong.

    Shape: {row_count, columns: [{name, type, raw_type, nulls, min, max,
    distinct, top_values}], date_column, date_min, date_max}. `type` is one of
    numeric | date | text | bool | other (ISO-looking text columns count as date).
    """
    try:
        types = await _column_types(db, table_name)
        names = list(columns or types.keys())[:_PROFILE_MAX_COLUMNS]
        if not names:
            return {}
        kinds = {c: _classify_type(types.get(c, "")) for c in names}
        parts = ["COUNT(*) AS n_rows"]
        for i, name in enumerate(names):
            q = _quote_ident(name)
            parts.append(f"COUNT({q}) AS nn_{i}")
            if kinds[name] in ("numeric", "date", "text"):
                parts.append(f"MIN({q}) AS mn_{i}")
                parts.append(f"MAX({q}) AS mx_{i}")
            if kinds[name] == "text":
                parts.append(f"COUNT(DISTINCT {q}) AS dc_{i}")
        table = _quote_ident(table_name)
        result = await db.execute(text(f"SELECT {', '.join(parts)} FROM {table}"))
        agg = result.mappings().first()
        if agg is None:
            return {}
        n_rows = int(agg["n_rows"] or 0)
        out_cols = []
        for i, name in enumerate(names):
            kind = kinds[name]
            entry = {
                "name": name,
                "type": kind,
                "raw_type": types.get(name, ""),
                "nulls": max(0, n_rows - int(agg[f"nn_{i}"] or 0)),
            }
            if kind in ("numeric", "date"):
                entry["min"] = _jsonable(agg[f"mn_{i}"])
                entry["max"] = _jsonable(agg[f"mx_{i}"])
            elif kind == "text":
                entry["distinct"] = int(agg[f"dc_{i}"] or 0)
                lo, hi = agg[f"mn_{i}"], agg[f"mx_{i}"]
                if (
                    isinstance(lo, str) and isinstance(hi, str)
                    and _ISO_DATE_RE.match(lo) and _ISO_DATE_RE.match(hi)
                ):
                    entry["type"] = "date"
                    entry["iso_text"] = True
                    entry["min"], entry["max"] = lo[:10], hi[:10]
            out_cols.append(entry)

        # Top values for low-cardinality text columns, so the model filters on
        # the exact spellings that exist instead of guessing them.
        tv_done = 0
        for entry in out_cols:
            if tv_done >= _PROFILE_TOP_VALUE_COLUMNS:
                break
            if entry["type"] != "text" or not (2 <= entry.get("distinct", 0) <= _PROFILE_TOP_VALUES):
                continue
            q = _quote_ident(entry["name"])
            top = await db.execute(
                text(
                    f"SELECT {q} AS v, COUNT(*) AS n FROM {table} "
                    f"WHERE {q} IS NOT NULL GROUP BY {q} ORDER BY n DESC, v LIMIT {_PROFILE_TOP_VALUES}"
                )
            )
            entry["top_values"] = [str(r["v"]) for r in top.mappings()]
            tv_done += 1

        date_cols = [c for c in out_cols if c["type"] == "date" and c.get("min") and c.get("max")]
        preferred = [c for c in date_cols if "date" in c["name"].lower()]
        chosen = (preferred or date_cols or [None])[0]
        profile = {"row_count": n_rows, "columns": out_cols}
        if chosen is not None:
            profile["date_column"] = chosen["name"]
            profile["date_min"] = chosen["min"]
            profile["date_max"] = chosen["max"]
        return profile
    except Exception as exc:
        logger.warning(f"Data profile unavailable: {exc}")
        try:
            await db.rollback()
        except Exception:
            pass
        return {}
