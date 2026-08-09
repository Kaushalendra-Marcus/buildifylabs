# Spec 05 — Natural-Language-to-SQL Generation & Safety Sandbox

**Status:** ✅ Implemented (B2 + B4) — `sanitize_sql()` hardened; `clean_sql_response()` complete;
`execute_sql()` + user-scoping live; B4's `POST /chat` wires the whole path end-to-end (real
per-file column metadata → `build_data_schema()` → prompt → execute → `INVALID_QUERY` graceful
message). The `sales`/`customers`/`orders` placeholder is now only a documented fallback.
**Source files:** `app/services/llm/sql_generator.py`, `app/middlewares/sql_sanitizer.py`,
`app/services/data/executor.py`, `app/routes/chat.py`

---

## 1. Problem Statement

Natural-language questions must become database queries without ever letting write, DDL, or
data-exfiltration-capable SQL reach Postgres — even though the SQL itself is model-generated and
therefore inherently untrusted input.

## 2. Functional Requirements

- **FR1:** Build a prompt giving the LLM a fixed, explicit schema and strict generation rules:
  SELECT-only, no `SELECT *`, mandatory `LIMIT 100`, raw SQL only, no markdown/comments/prose.
  *(Done — `build_sql_prompt`.)*
- **FR2:** Strip any markdown fences or commentary the model adds despite instructions, leaving
  only the bare SQL string. *(Done — `clean_sql_response`, handles plain / fenced / prose-wrapped.)*
- **FR3:** Pass the cleaned SQL through `sanitize_sql()` before execution. *(Done.)* It:
  - Parses via `sqlglot` (dialect: `postgres`) into a real AST — not a regex/keyword blacklist.
  - Rejects anything that isn't exactly one top-level `SELECT` / `UNION` / `INTERSECT` / `EXCEPT`.
  - Walks the **full** AST (not just the top level) to catch write/DDL statements smuggled inside
    CTEs or subqueries.
  - Blocks a fixed list of dangerous Postgres functions (`pg_sleep`, `pg_read_file`, `dblink`,
    `lo_import`/`lo_export`, `pg_terminate_backend`, etc.).
  - Enforces a max query length (1000 chars) and requires an explicit `LIMIT` clause.
- **FR4:** If the model can't produce a valid query for the question, it must return the sentinel
  `SELECT 'INVALID_QUERY' LIMIT 1` rather than guessing. *(Done — `INVALID_QUERY_SENTINEL`
  constant + `is_invalid_query()`; the executor short-circuits and never runs it.)*
- **FR5:** Execute the sanitized query and return rows as `db_data` for the AI pipeline (spec `06`).
  *(Done — `execute_sql()`, user-scoped.)*

## 3. API Contracts (internal function contracts — no HTTP surface of its own)

| Function | Status | Contract |
|---|---|---|
| `build_sql_prompt(user_query: str, schema: str \| None = None) -> str` | ✅ Done | Input: raw NL question, optional pre-built schema (default: placeholder `DEFAULT_DATABASE_SCHEMA`). Output: full prompt string including schema + rules. |
| `build_data_schema(table_name: str, columns: list[str]) -> str` | ✅ Done | Builds the prompt's schema section for one user's data table. B3 feeds it real per-file columns after ingestion. |
| `clean_sql_response(text: str) -> str` | ✅ Done | Input: raw LLM text, possibly fenced (` ```sql ... ``` `) or with trailing/leading prose. Output: a single bare SQL string, no fences/comments/prose. Text-cleanup only. |
| `sanitize_sql(query: str) -> str` | ✅ Done | Input: cleaned SQL string. Raises `HTTPException` (400 parse/empty/too-long, 403 forbidden statement/function/no-LIMIT). Returns the query unchanged on success. |
| `assert_user_scoped(query: str, user_table: str) -> None` | ✅ Done | Input: cleaned SQL + the caller's data-table name. Raises 403 unless every non-CTE table reference is the caller's own table (in the `public` schema). |
| `execute_sql(query: str, db: AsyncSession, user_table: str) -> list[dict]` | ✅ Done | Runs the sanitized, user-scoped SELECT; returns rows as dicts; `[]` on empty result. Raises `InvalidQueryError` on the sentinel; 422 on execution errors (e.g. hallucinated columns); never returns another user's rows. |

## 4. Constraints

- The `DATABASE_SCHEMA` string currently hardcoded in `sql_generator.py` (`sales`, `customers`,
  `orders`) is a **placeholder** fallback. The real path is dynamic: `build_data_schema()` builds
  the prompt's schema section from the **user's own uploaded data table** (named by
  `user_data_table_name()`), and `build_sql_prompt(schema=...)` consumes it. B3 fills in the actual
  column metadata once file ingestion ships.
- `clean_sql_response` must stay text-cleanup only — safety logic is centralized in
  `sanitize_sql` and must not be duplicated or second-guessed in the generator.
- The 1000-character max length and mandatory `LIMIT` are hard constraints enforced downstream
  regardless of what the generator produces.

## 5. Edge Cases & Error Handling

1. **LLM wraps output in ` ```sql ... ``` ` fences** despite instructions → stripped by
   `clean_sql_response` (`_strip_fences`).
2. **LLM appends explanatory prose after the SQL** → `clean_sql_response` grows the text line by
   line and keeps the longest prefix that parses as exactly one statement, so prose is dropped while
   genuine multi-line SQL is preserved. If extraction fails, `sanitize_sql` rejects it anyway
   (fails to parse as a single statement).
3. **LLM returns the `INVALID_QUERY` sentinel** → `is_invalid_query()` detects it (normalized
   exact match, tolerating semicolons/whitespace/case) and `execute_sql()` short-circuits with
   `InvalidQueryError` — the pipeline turns that into a user-facing "couldn't turn that into a
   query" message (wired end-to-end in B4's `/chat`), never executing it or returning the string
   as if it were real data.
4. **LLM hallucinates a column/table not in the provided schema** → `sanitize_sql` validates
   structural/syntactic safety only; the failure surfaces at execution time. `execute_sql()`
   catches it and raises a clean 422 (logged server-side), never a 500. Table references that name
   *any* table other than the caller's own are rejected earlier as a 403 by `assert_user_scoped`.
5. **[Closed] No automatic user-scoping.** Closed in two layers (co-designed with B3 storage):
   **structurally** — each user's uploaded data lives in a dedicated per-user table
   (`user_data_table_name(user_id)`), so a query against it physically cannot read another user's
   rows; and **post-generation validation** — `assert_user_scoped()` walks the AST and rejects any
   query referencing a table outside the caller's namespace (`users`, `payments`, `query_logs`,
   another user's table, or a foreign schema).

## 6. Acceptance Criteria

- [x] `clean_sql_response` reliably extracts bare SQL from: plain SQL, fenced SQL, and SQL with
      trailing prose — covered by unit tests with all three input shapes (`Backend/tests/test_clean_sql_response.py`).
- [x] 100% of write/DDL attempts (including ones smuggled inside CTEs/subqueries) are rejected by
      `sanitize_sql` — covered by a regression test suite (`Backend/tests/test_sql_sanitizer_regression.py`).
- [x] A generated query can **never** return another user's rows (gap #5 above is closed and
      tested — `Backend/tests/test_user_scoping.py` + `test_executor.py`).
- [x] The `INVALID_QUERY` sentinel is handled gracefully end-to-end, with a clear user-facing
      message. *(Executor short-circuits with `InvalidQueryError` (B2); B4's `POST /chat` catches
      it and returns a `confidence = 0.0` fallback "couldn't turn that into a query" message,
      still writing the QueryLogs row so the failure is observable.)*
