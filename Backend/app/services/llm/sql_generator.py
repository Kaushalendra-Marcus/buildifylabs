import logging
import re
from typing import Optional, List

import sqlglot

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


def build_data_schema(table_name: str, columns: List[str]) -> str:
    """Build the prompt's schema section for one user's data table.

    B3 calls this after ingesting a file, with the real per-user table name and
    its parsed columns, replacing the hardcoded placeholder schema.
    """
    lines = ["Tables:", table_name]
    lines.extend(f"- {column}" for column in columns)
    return "\n".join(lines)


# Build Prompt

def build_sql_prompt(user_query: str, schema: Optional[str] = None) -> str:
    schema = schema or DEFAULT_DATABASE_SCHEMA
    return f"""
    Database Schema:
    {schema}
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
