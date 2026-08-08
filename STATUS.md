# BuildifyLabs — STATUS

Tracks one-task-at-a-time progress against
[`implementation-plan-master.md`](./implementation-plan-master.md). Source of truth for product
requirements is `specs/`; re-read only the relevant plan/phase before each run.

Legend: ✅ completed · ⚠️ partial · ⛔ blocked · ⏸ deferred/paused

## Current task

**Phase B3 — File upload + minimal ingestion** `[IMMEDIATE]` (build step 5, `specs/04`)
— not started this run. `POST /files/upload` (non-guest, 0-byte/415/413 checks), storage-backend
decision, minimal CSV parse → create the **per-user data table** the B2 SQL layer already queries
(use `executor.user_data_table_name()`), `FileUpload.status` transitions + `error` column.

## Completed tasks

- **B2 — SQL generation + execution + user-scoping** — done, test-verified:
  - **`sql_generator.py`** (`app/services/llm/sql_generator.py`): `clean_sql_response()` now
    extracts a single bare SQL statement from plain / fenced (` ```sql `) / prose-wrapped model
    output by keeping the longest prefix that parses as exactly one statement (text-cleanup only).
    `INVALID_QUERY_SENTINEL` + `is_invalid_query()` (normalized exact match) replace the hardcoded
    sentinel. `build_data_schema(table, columns)` + `build_sql_prompt(query, schema=...)` make the
    prompt schema dynamic; `DEFAULT_DATABASE_SCHEMA` (`sales/customers/orders`) is now only a
    documented fallback until B3 feeds real column metadata.
  - **`app/services/data/executor.py`** (new): `user_data_table_name(user_id)` — deterministic
    per-user data table (`user_<uuidhex>_data`), the **co-designed B3 storage contract**;
    `assert_user_scoped(query, user_table)` — AST walk rejects (403) any non-CTE table reference
    outside the caller's namespace (shared app tables, another user's table, foreign schemas);
    `execute_sql(query, db, user_table) -> list[dict]` — composes `is_invalid_query` →
    `sanitize_sql` → `assert_user_scoped` → execute; empty result → `[]`; `InvalidQueryError` on
    the sentinel; Postgres execution errors (hallucinated columns) → clean 422 with rollback.
  - **The blocking user-scoping gap (`specs/05` §5.5) is closed**: structural per-user tables
    (B3 creates them) + post-generation validation. A generated query can never read another
    user's rows.
  - **Test suite established** at `Backend/tests/` (76 tests pass, `python -m pytest` from
    `Backend/`): `clean_sql_response` (plain/fenced/prose/edge), `sanitize_sql` regression
    (write/DDL incl. CTE smuggling, forbidden functions), `assert_user_scoped`, and `execute_sql`
    end-to-end on in-memory SQLite (rows, empty, sentinel, 403 foreign table/write, 422 bad
    column). `conftest.py` handles `sys.path`; no pytest-asyncio needed.
  - `specs/05` status, §3 contract, §5 edge cases + §6 checkboxes updated in the same change;
    `specs/00` module map + cross-cutting gap #5, `docs/conventions.md` (scoping invariant),
    `docs/known-gaps.md`, `Backend/CLAUDE.md` invariant, and the master plan's module state updated.
- **B1 — Quota rewrite + contact flow** — done, smoke-verified:
  - **`User` model** (`app/db/models/user.py`): added `questions_in_window`,
    `window_started_at`, `questions_lifetime`; removed `queries_today`, `last_reset`. Migration
    `alembic/versions/b1code0000_quota_rolling_window.py`.
  - **`usage.py`** now the single source of truth for the window rule: `QUOTA_WINDOW` /
    `window_elapsed_clause()` (SQLAlchemy form for the atomic UPDATE) / `window_reset_at()`.
    Old `reset_daily_usage_if_needed` removed.
  - **`rate_limiter.py`** rewritten: everyone gets 4-per-6h + 100-lifetime, no `plan` branching;
    one atomic `UPDATE ... WHERE ... RETURNING` over **both** counters + the roll condition (the
    same statement rolls `window_started_at` and resets `questions_in_window` when 6h elapse, so a
    count can't land against a just-rolled window). Emits `429 {detail}` (window, with reset time)
    and `429 {detail, contact_form: true}` (lifetime) — raised as `QuotaLimitExceeded`, handled
    app-wide in `main.py` so the body matches the `specs/02` §3 contract exactly.
  - **`guest_auth.py`** — dropped `GUEST_DAILY_LIMIT` and the old sign-in daily check; guests use
    the same window logic tracked by `device_fingerprint` (best-effort lifetime — accepted).
  - **`plan_checker.py`** — stays dormant; now logs a warning on unrecognized `plan` values.
  - **`POST /contact`** — `{name,email,message}` → email via existing `email_sender.py` async SMTP
    to new required `CONTACT_FORM_RECIPIENT_EMAIL` config; no verification (low-stakes lead capture).

## Next task

**Phase B4 — End-to-end `POST /chat`** `[IMMEDIATE]` (build step 6; `05`+`06`+`11` §3.1+`10` §2):
the first working demo loop — `rate_limiter` → build SQL prompt → LLM → `clean_sql_response` →
`sanitize_sql` → user-scoped `execute_sql` → stats (pandas) → `run_pipeline` → response, with trust
requirements (traceable SQL, hedged causal language, `QueryLogs` writes, flag mechanism,
`clarification`) built in. `INVALID_QUERY` sentinel → graceful message.

## Blocked / deferred

- **Spec-01 completeness (single-use reset tokens; resend-verification endpoint)** — ⏸ decided OUT
  of B0 at execution (plan: "decide in/out at execution"; known-gaps, not on critical path).
- **Phase B9 payments / F7 upgrade UI** — ⏸ paused (`specs/03`).
- **Phases B5–B8, F7–F9** — 🔴 post-checkpoint (`specs/00` §7); do not start before real-user checkpoint.
- **B4 → frontend F8 live source-scope** — 🔴 gated on check B7.
- **B7 `source_scope` beyond `own_data`** — needs Pinecone+Redis (`specs/07`).

## Important decisions

- **Per-user data tables (B2↔B3 co-design):** each user's uploaded data lands in a dedicated table
  `user_<uuid-hex>_data` (`executor.user_data_table_name()`). User-scoping is *structural* (that
  table only ever holds the owner's rows) plus post-generation `assert_user_scoped()` validation —
  resolving the "inject `WHERE user_id` vs per-user table" question in favor of per-user tables
  (`specs/05` §5.5). Also satisfies `specs/08` FR5 later.
- **Dynamic schema:** `build_sql_prompt(schema=...)` + `build_data_schema(table, columns)` exist;
  B3 supplies real per-file column metadata. Until then the `sales/customers/orders`
  `DEFAULT_DATABASE_SCHEMA` is a documented placeholder only.
- `GROQ_MODEL` interim = `llama-3.3-70b-versatile`; retires **2026-08-16** → pick a durable model in B5.
- Quota constants (`4` / `6h` / `100`) are a **module decision** in `app/utils/usage.py` (config only
  defines auth rate-limit *counts*); single source of truth for the window rule stays in one place.
- Guest lifetime cap is best-effort (`device_fingerprint`) — accepted tradeoff, `specs/02` §5.
- In-memory per-instance auth limiter is MVP-acceptable; swap to Redis (shared store) with B7.
- Environment gap found: `requests` needed by `google-auth` is not in `requirements.txt` (installed
  only in a `/tmp` temp venv for verification — not modified). Tracked; not part of B1.

## Tests / verification (this run)

A real pytest suite now lives at `Backend/tests/` (76 tests, run from `Backend/` via
`python -m pytest`; temp venv `/tmp/opencode/blvenv`, Python 3.12, `sqlglot` 30.15, `aiosqlite`):

- `clean_sql_response` — plain / fenced / prose-after / prose-before / multi-line / sentinel /
  garbage → 22 assertions green.
- `sanitize_sql` regression — valid SELECTs pass; INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE
  rejected (400 parse or 403 AST), write/DDL **smuggled in CTEs** rejected, forbidden functions
  (`pg_sleep`, `pg_read_file`, `dblink`, `lo_export`, `pg_terminate_backend`, incl. inside
  subqueries) rejected, two-statements/leading-paren rejected.
- `assert_user_scoped` — own table (incl. `public.`-qualified, case-insensitive) and CTEs allowed;
  `users`/`payments`/`query_logs`/another user's table/foreign schema/`"USERS"` → 403.
- `execute_sql` E2E on in-memory SQLite using the exact `user_data_table_name()` table: rows → dicts,
  empty → `[]`, sentinel → `InvalidQueryError`, foreign table/write → 403, hallucinated column → 422.
- `import app.main` / all B2 modules — OK (dummy env vars).
- Frontend still scaffold-only; mixing not exercised.

## Last updated

2026-08-09 (B2 complete — see `git diff` for the exact change set)
