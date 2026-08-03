import logging

import sqlglot
from sqlglot import exp
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 1000
SQL_DIALECT = "postgres"

# Statement types that are allowed at the top level - anything that is
# fundamentally a read-only query (including CTEs and set operations).
ALLOWED_TOP_LEVEL = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Any of these appearing *anywhere* in the parsed tree (not just at the top
# level - e.g. smuggled inside a CTE) means this isn't a pure read query.
FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Command,  # catch-all sqlglot uses for statements it doesn't model
    # explicitly (GRANT, VACUUM, SET, COPY, etc.)
)

# Function calls that are technically read-only SELECTs but can be used to
# exfiltrate data, hang the connection, or touch the filesystem/network.
FORBIDDEN_FUNCTION_NAMES = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_connect",
    "copy",
}


def sanitize_sql(query: str) -> str:
    """Validate that `query` is a single, safe, read-only SELECT.

    This used to be a regex blacklist over the uppercased query text, which
    is inherently fragile - it can't tell a forbidden keyword sitting inside
    a string literal from a real one, doesn't understand statements nested
    inside CTEs/subqueries, and is trivially bypassed by any SQL syntax the
    author didn't think to test. Parsing the query into a real AST and
    checking its structure closes all of that off at once.
    """
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Empty query")

    if len(query) > MAX_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="Query too long")

    try:
        statements = [s for s in sqlglot.parse(query, read=SQL_DIALECT) if s is not None]
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse SQL query")

    if len(statements) != 1:
        raise HTTPException(
            status_code=403,
            detail="Exactly one statement is allowed",
        )

    statement = statements[0]

    if not isinstance(statement, ALLOWED_TOP_LEVEL):
        raise HTTPException(
            status_code=403,
            detail="Only SELECT queries are allowed",
        )

    for node in statement.walk():
        node = node[0] if isinstance(node, tuple) else node

        if isinstance(node, FORBIDDEN_EXPRESSIONS):
            logger.warning(f"Blocked dangerous query (forbidden statement): {query}")
            raise HTTPException(
                status_code=403,
                detail="Forbidden SQL operation detected",
            )

        if isinstance(node, (exp.Anonymous, exp.Func)):
            func_name = (node.name or "").lower()
            if func_name in FORBIDDEN_FUNCTION_NAMES:
                logger.warning(f"Blocked dangerous query (forbidden function): {query}")
                raise HTTPException(
                    status_code=403,
                    detail="Forbidden SQL function detected",
                )

    if not statement.args.get("limit"):
        raise HTTPException(
            status_code=403,
            detail="Query must include LIMIT",
        )

    return query
