import logging
import re
from typing import Optional, List

import sqlglot
from sqlglot import exp

from app.middlewares.sql_sanitizer import SQL_DIALECT

logger = logging.getLogger(__name__)

# The exact statement the model is told to return when it can't turn the user's
# question into a query (specs/05 FR4). Shared with the execution layer so a
# generated query is never run against the database and returned as real data.
INVALID_QUERY_SENTINEL = "SELECT 'INVALID_QUERY' LIMIT 1"

# Placeholder schema used until B3 feeds real per-file metadata through
# build_data_schema()/build_sql_prompt(). Does NOT reflect the app's real
# tables - the user's uploaded data lives in a per-user table (see
# app/services/data/executor.py) whose columns are described by build_data_schema().
DEFAULT_DATABASE_SCHEMA = """
Tables:
sales
- id
- date
- revenue
- region
- product

customers
- id
- name
- city
- created_at

orders
- id
- customer_id
- amount
- status
- created_at
"""

SQL_SYSTEM_PROMPT = f"""
You are an expert PostgreSQL SQL generator.
Your only job:
Generate SAFE read-only SQL queries.

STRICT RULES:
- Only generate SELECT queries
- Never generate DROP, DELETE, UPDATE, ALTER, INSERT
- Never use SELECT *
- Always include LIMIT 100
- Use only tables and columns provided
- Return only raw SQL
- No markdown
- No explaination
- No comments
- Use PostgreSQL syntax

If query is impossible:
Return:
{INVALID_QUERY_SENTINEL}
"""

_SQL_START_RE = re.compile(r"^(select|with|union|intersect|except)\b", re.IGNORECASE)

_FENCE_RE = re.compile(r"```[a-zA-Z]*\r?\n(.*?)```", re.DOTALL)
_INLINE_FENCE_RE = re.compile(r"```[a-zA-Z]*[ \t]*(.*?)```", re.DOTALL)


def build_data_schema(table_name: str, columns: List[str], profile: Optional[dict] = None) -> str:
    """Build the prompt's schema section for one user's data table.

    B3 calls this after ingesting a file, with the real per-user table name and
    its parsed columns, replacing the hardcoded placeholder schema. When a data
    profile is given (executor.get_data_profile), column types and real value
    facts are added; without one the output is exactly the plain column list.
    """
    lines = ["Tables:", table_name]
    kinds = {}
    if isinstance(profile, dict):
        for col in profile.get("columns") or []:
            if isinstance(col, dict) and col.get("name"):
                kinds[col["name"]] = (
                    "text holding ISO dates (YYYY-MM-DD); compare as strings"
                    if col.get("iso_text")
                    else col.get("type")
                )
    for column in columns:
        kind = kinds.get(column)
        lines.append(f"- {column} ({kind})" if kind else f"- {column}")
    block = profile_prompt_block(profile)
    if block:
        lines.append(block)
    return "\n".join(lines)


def profile_prompt_block(profile: Optional[dict]) -> str:
    """Real facts about the table for the SQL prompt. Empty when no profile."""
    try:
        if not isinstance(profile, dict) or not profile.get("columns"):
            return ""
        lines = ["", "Data profile (real facts about this table - use them, never guess values):"]
        if isinstance(profile.get("row_count"), int):
            lines.append(f"- row count: {profile['row_count']}")
        for col in list(profile["columns"])[:40]:
            if not isinstance(col, dict):
                continue
            name, kind = col.get("name"), col.get("type", "unknown")
            shown_kind = "text, ISO dates" if col.get("iso_text") else kind
            detail = []
            if kind in ("numeric", "date") and col.get("min") is not None:
                joiner = " to " if kind == "date" else " .. "
                detail.append(f"{col.get('min')}{joiner}{col.get('max')}")
            if kind == "text" and col.get("distinct") is not None:
                detail.append(f"{col['distinct']} distinct")
                if col.get("top_values"):
                    detail.append("values: " + ", ".join(f"'{v}'" for v in col["top_values"]))
            if col.get("nulls"):
                detail.append(f"{col['nulls']} empty")
            lines.append(f"- {name} ({shown_kind})" + (": " + "; ".join(detail) if detail else ""))
        if profile.get("date_max"):
            lines.append(
                f"Latest date in the data: {profile['date_max']} (column {profile.get('date_column')}). "
                "Resolve relative dates (\"last week\", \"this month\", \"last quarter\", "
                "\"yesterday\", \"recent\") against THIS date, never CURRENT_DATE or NOW()."
            )
        lines.append("When filtering a text column, use exactly the listed values (case-sensitive).")
        return "\n".join(lines)
    except Exception:
        return ""


def describe_sql(query: str) -> str:
    """Plain-English reading of a generated query, built from its AST (no LLM).

    Shown to the user so a wrong interpretation ("I summed revenue where
    status = paid") is easy to spot. Never raises; empty string on any failure.
    """
    try:
        ast = sqlglot.parse_one(query, read=SQL_DIALECT)
        select = ast if isinstance(ast, exp.Select) else ast.find(exp.Select)
        if select is None:
            return ""

        def _sql(node) -> str:
            return node.sql(dialect=SQL_DIALECT)

        parts = []
        projections = [_sql(e) for e in (select.expressions or [])[:6]]
        if projections:
            parts.append("selects " + ", ".join(projections))
        where = select.args.get("where")
        if where is not None:
            parts.append("filtered to " + _sql(where.this))
        group = select.args.get("group")
        if group is not None and group.expressions:
            parts.append("grouped by " + ", ".join(_sql(e) for e in group.expressions))
        order = select.args.get("order")
        if order is not None and order.expressions:
            parts.append("ordered by " + ", ".join(_sql(e) for e in order.expressions))
        limit = select.args.get("limit")
        limit_expr = limit.args.get("expression") if limit is not None else None
        if limit_expr is not None:
            parts.append(f"limited to {limit_expr.name} rows")
        if not parts:
            return ""
        text = "Reads your data table: " + ", ".join(parts) + "."
        return re.sub(r'"?user_[0-9a-zA-Z]+_data"?', "your data", text)
    except Exception:
        return ""


# Build Prompt

def build_sql_prompt(
    user_query: str,
    schema: Optional[str] = None,
    prior_query: Optional[str] = None,
) -> str:
    schema = schema or DEFAULT_DATABASE_SCHEMA
    if not prior_query or not prior_query.strip():
        return f"""
    Database Schema:
    {schema}
    User Query:
    {user_query}
    Generate a safe PostgreSQL query.
"""
    prior_section = f"""
    Previous question in this conversation (context only):
    {prior_query.strip()}

    If the question below is a follow-up that refers back to the previous one
    (pronouns like "that"/"it", or an incomplete phrase like "what about next
    month" or "break that down by region"), resolve it using the previous
    question's subject, metric, and time period. If the question below is a
    complete, self-contained question on its own topic, ignore the previous
    question entirely.
"""
    return f"""
    Database Schema:
    {schema}
    {prior_section}
    User Query:
    {user_query}
    Generate a safe PostgreSQL query.
"""


def _strip_fences(text: str) -> str:
    """Pull the content out of a ```sql ... ``` block, if the text is fenced.

    Text-cleanup only - never a safety decision (see docs/conventions.md).
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    for pattern in (_FENCE_RE, _INLINE_FENCE_RE):
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return text


def _extract_sql_statement(text: str) -> str:
    """Extract the single SQL statement from text that may wrap it in prose.

    Grows the text line by line (from the first line that looks like a SQL
    statement) and keeps the longest prefix that parses as exactly one
    statement, so trailing prose is dropped while genuine multi-line SQL is
    preserved. Fails safe: unparseable input yields "" and sanitize_sql()
    rejects it with a clean error.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _SQL_START_RE.match(line.strip()):
            start = i
            break
    if start is None:
        return ""

    best = ""
    buffer: List[str] = []
    for line in lines[start:]:
        buffer.append(line)
        candidate = "\n".join(buffer)
        try:
            statements = [s for s in sqlglot.parse(candidate, read=SQL_DIALECT) if s is not None]
        except Exception:
            continue
        if len(statements) == 1:
            best = candidate
        elif len(statements) > 1:
            break

    return best.strip()


# clean sql response
# eg. llm may return like this: ```sql
# SELECT id, name FROM customers;
# ```
def clean_sql_response(text: str) -> str:
    """
    Remove markdown/codeblocks/explaination
    """
    if not text or not text.strip():
        return ""
    return _extract_sql_statement(_strip_fences(text))


def is_invalid_query(query: str) -> bool:
    """True if the model returned the INVALID_QUERY sentinel instead of SQL.

    Compares a whitespace-normalized, case-folded form so variations like a
    trailing semicolon or extra spacing still match.
    """
    normalized = re.sub(r"\s+", " ", query.strip()).strip().rstrip(";").strip()
    sentinel = re.sub(r"\s+", " ", INVALID_QUERY_SENTINEL).strip()
    return normalized.lower() == sentinel.lower()
