# Spec 05 — Natural-Language-to-SQL Generation & Safety Sandbox

**Status:** ⚠️ Partially implemented — `sanitize_sql()` is complete and hardened;
`sql_generator.clean_sql_response()` is an unfinished stub; there is no execution step.
**Source files:** `app/services/llm/sql_generator.py`, `app/middlewares/sql_sanitizer.py`

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
  only the bare SQL string. *(Not implemented — `clean_sql_response` body is empty.)*
- **FR3:** Pass the cleaned SQL through `sanitize_sql()` before execution. *(Done.)* It:
  - Parses via `sqlglot` (dialect: `postgres`) into a real AST — not a regex/keyword blacklist.
  - Rejects anything that isn't exactly one top-level `SELECT` / `UNION` / `INTERSECT` / `EXCEPT`.
  - Walks the **full** AST (not just the top level) to catch write/DDL statements smuggled inside
    CTEs or subqueries.
  - Blocks a fixed list of dangerous Postgres functions (`pg_sleep`, `pg_read_file`, `dblink`,
    `lo_import`/`lo_export`, `pg_terminate_backend`, etc.).
  - Enforces a max query length (1000 chars) and requires an explicit `LIMIT` clause.
- **FR4:** If the model can't produce a valid query for the question, it must return the sentinel
  `SELECT 'INVALID_QUERY' LIMIT 1` rather than guessing.
- **FR5:** Execute the sanitized query and return rows as `db_data` for the AI pipeline (spec `06`).
  *(Not implemented — no execution function exists yet.)*

## 3. API Contracts (internal function contracts — no HTTP surface of its own)

| Function | Status | Contract |
|---|---|---|
| `build_sql_prompt(user_query: str) -> str` | ✅ Done | Input: raw NL question. Output: full prompt string including schema + rules. |
| `clean_sql_response(text: str) -> str` | ❌ Stub | Input: raw LLM text, possibly fenced (` ```sql ... ``` `) or with trailing prose. Output: a single bare SQL string, no fences/comments/prose. |
| `sanitize_sql(query: str) -> str` | ✅ Done | Input: cleaned SQL string. Raises `HTTPException` (400 parse/empty/too-long, 403 forbidden statement/function/no-LIMIT). Returns the query unchanged on success. |
| `execute_sql(query: str, db: AsyncSession) -> list[dict]` | ❌ Missing | Needs to be written. Must respect the sanitizer's enforced `LIMIT`; must not fail the whole pipeline on an empty result set. |

## 4. Constraints

- The `DATABASE_SCHEMA` string currently hardcoded in `sql_generator.py` (`sales`, `customers`,
  `orders`) is a **placeholder** — it does not reflect the app's real tables (`users`,
  `file_uploads`, `payments`, `query_logs`), nor does it yet describe the **user's own uploaded
  business data**, which is what it actually needs to describe once file ingestion (spec `04`) is
  built. This schema must be generated dynamically per user/file, not hardcoded.
- `clean_sql_response` must stay text-cleanup only — safety logic is centralized in
  `sanitize_sql` and must not be duplicated or second-guessed in the generator.
- The 1000-character max length and mandatory `LIMIT` are hard constraints enforced downstream
  regardless of what the generator produces.

## 5. Edge Cases & Error Handling

1. **LLM wraps output in ` ```sql ... ``` ` fences** despite instructions → must be stripped by
   `clean_sql_response` (currently unhandled).
2. **LLM appends explanatory prose after the SQL** → must be stripped/extracted; if extraction
   fails, `sanitize_sql` will reject it anyway (fails to parse as a single statement), but a clean
   extraction should be attempted first for better UX.
3. **LLM returns the `INVALID_QUERY` sentinel** → the pipeline must short-circuit with a
   user-facing "couldn't turn that into a query" message, not execute it and return the string
   `"INVALID_QUERY"` as if it were real data.
4. **LLM hallucinates a column/table not in the provided schema** → `sanitize_sql` only validates
   structural/syntactic safety, not schema correctness — this fails at execution time with a
   Postgres error, which must be caught and turned into a clean user-facing error, not a `500`.
5. **[Blocking gap] No automatic user-scoping.** Nothing currently forces a generated query to
   filter to the requesting user's own uploaded data — the LLM is not given a mandatory
   `WHERE user_id = :current_user` (or equivalent per-file scope) constraint. **This must be closed
   before this module is safe for multi-tenant use** — either by injecting a mandatory scope into
   the prompt + validating it's present post-generation, or by executing against a
   per-user/per-file table or schema rather than a shared one.

## 6. Acceptance Criteria

- [ ] `clean_sql_response` reliably extracts bare SQL from: plain SQL, fenced SQL, and SQL with
      trailing prose — covered by unit tests with all three input shapes.
- [ ] 100% of write/DDL attempts (including ones smuggled inside CTEs/subqueries) are rejected by
      `sanitize_sql` — already true by design; needs an explicit regression test suite.
- [ ] A generated query can **never** return another user's rows (gap #5 above is closed and
      tested).
- [ ] The `INVALID_QUERY` sentinel is handled gracefully end-to-end, with a clear user-facing
      message.
