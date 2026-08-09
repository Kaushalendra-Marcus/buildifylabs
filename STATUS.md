# BuildifyLabs — STATUS

Tracks one-task-at-a-time progress against
[`implementation-plan-master.md`](./implementation-plan-master.md). Source of truth for product
requirements is `specs/`; re-read only the relevant plan/phase before each run.

Legend: ✅ completed · ⚠️ partial · ⛔ blocked · ⏸ deferred/paused

## Current task

**Phase F0 — Frontend foundations + type-contract freeze** `[IMMEDIATE]` (master plan Part F; pre-checkpoint,
buildable now): make deliberate stack choices once (router, Zustand, fetch wrapper, CSS, Recharts, lucide-react,
Google Identity Services, Vitest/RTL), stand up `src/{api,types,lib/schemas,components,features,hooks}`, replace
boilerplate `App.tsx`, implement design-token roles (`specs/14` §7), and freeze the shared type contract —
create `src/lib/schemas/visuals.ts` as the per-`visual_type` props source of truth (the Chat section of
`Frontend/docs/type-contracts.md` was already corrected to the 7 types in B4; `visuals.ts` itself is the
outstanding piece). Mirror auth/upload/chat/payment types in `src/types/`; build `api/*.ts` mocks behind the real
contract signatures (auth/quota/upload/chat are now live and one-file-swappable).

## Completed tasks

- **B4 — End-to-end `POST /chat`** — done, test-verified (**149 tests**, up from 112):
  - **`langchain_pipeline.py` migrated to the `specs/06` §3 contract** — 7 real `visual_type`s
    (`metric`/`graph`/`table`/`comparison`/`insight`/`alert`/`status`) with `props: Dict` (no `chart_data`);
    `confidence` `Field(ge=0.0, le=1.0)`; `clarification: Optional[ClarificationRequest]` alternate mode;
    `run_pipeline(..., source_scope="own_data", company_name=None)` (old `include_news` gone); mutable default
    `news_context: list = []` → `None`; `SYSTEM_PROMPT` teaches the 7 types + hedged causal language
    (`specs/10` §2); `_truncate_rows` caps prompt rows at 50 (edge case 6).
  - **`app/routes/chat.py`** (new): `POST /chat` — `rate_limiter` (quota) → SQL prompt from the user's **real**
    uploaded columns (`get_table_columns` → `build_data_schema`) → LLM → `clean_sql_response` → `sanitize_sql`
    → user-scoped `execute_sql` → deterministic pandas stats (`specs/11` §3.1, `app/services/data/stats.py`) →
    `run_pipeline` → `PipelineOutput`. `INVALID_QUERY` → graceful fallback message. Every request+response
    written to `QueryLogs` (incl. fallbacks). `source_scope` = `own_data` only (B7 deferred). **Trust
    requirements (`specs/10` §2) built in:** `sql_query` + `data_preview` on the response (real "show the
    query"), hedged language, `clarification` as a working mode.
  - **`POST /chat/flag`** — own-only flag (other user → 404) setting new `QueryLogs.flagged` column; migration
    `b4code0000_query_logs_flag.py` (`alembic heads` = `b4code0000`).
  - **`app/services/data/executor.py::get_table_columns()`** — introspects real per-file columns
    (information_schema → PRAGMA fallback); the `sales/customers/orders` placeholder is now only a fallback.
  - **`rate_limiter` fix surfaced by `/chat`:** the atomic quota `UPDATE` needed
    `synchronize_session=False` — its ORM evaluate path compared a SQLite-loaded naive `window_started_at`
    against the tz-aware `now` and crashed on commit (SQL unchanged; Postgres unaffected).
  - **Tests:** `test_stats.py` (deterministic stats incl. datetime/NaN), `test_pipeline_contract.py` (7 types,
    bounded confidence, truncation, fallbacks, clarification mode, mutable-default regression),
    `test_chat_api.py` (10 e2e: happy loop, clarification, invalid query, source_scope fallback, flag own/other,
    quota 429 window + lifetime, no-data response). `conftest.py` adds dummy `GROQ_API_KEY`/`HF_API_KEY` (the
    Groq client is constructed at import time).
  - **Docs updated in the same change:** `specs/05`, `06`, `10`, `11`, `00` module map + build order,
    `implementation-plan-master.md` (contract table, module state, B4 section, risks #5–6), `Backend/CLAUDE.md`,
    `Frontend/CLAUDE.md` (chat + upload now live), `Frontend/docs/type-contracts.md` Chat section (7 types),
    `Backend/docs/known-gaps.md`.

- **B3 — File upload + minimal ingestion** — done, test-verified (112 tests):
  - **`app/routes/files.py`** (new): `POST /files/upload` → **202** `FileResponse`; `GET /files`,
    `GET /files/{id}` (own-only, else 404). Row created up front (`processing`); ingestion either
    sets `completed` (with the per-user table ref in `pinecone_namespace`, until Pinecone) or
    `failed` + trimmed `error` — never stuck on `processing` (`specs/04` edge case 1).
  - **`file_validator.py`** — now `400` for **0-byte** files; type check tightened to a **per-type
    EXT↔MIME mapping** so a `.csv`+`application/pdf` mismatched pair is `415` by design (`specs/04`
    §4). Guest/invalid plan → 403; free 3MB / pro 10MB → 413 unchanged.
  - **Storage backend (gap #4 resolved)** — `app/services/data/storage.py`: **local disk for dev**
    (`UPLOAD_DIR` config), object store for prod; the module is the swap seam. Raw uploads persist
    as `<user_id>/<upload_id><ext>` (never the caller's filename → no path traversal).
  - **`app/services/data/parser.py`** (new): pandas CSV parse (BOM/utf-8/latin-1 fallback, ragged-row
    tolerant, empty/`ParserError` → clean `ValueError`) → defensive clean (columns normalized to
    snake_case, all-NaN rows and full-dupe rows dropped, string→date + ₹/`,`-currency coercion) →
    **drop-and-recreate the user's typed data table** via B2's `user_data_table_name()` (columns
    Integer/Float/Boolean/DateTime/Text by pandas dtype) and bulk-insert. A fresh upload **replaces**
    the user's data table (one-active-file scope, `specs/04` §4). `.xlsx`/`.pdf` → `failed` with
    reason "not supported yet".
  - **`FileUpload.error` column** (String(500)) + migration `b3code0000_file_upload_error.py`
    (`alembic heads` = `b3code0000`; adds `ALTER TABLE file_uploads ADD COLUMN error VARCHAR(500)`);
    `FileResponse` gains `error`. `main.py` wires the files router.
  - **B2↔B3 co-design delivered:** `execute_sql()` runs against B2's tables; small smoke script proved
    B2's `execute_sql` returns correct aggregates (SUM/GROUP BY) on B3's ingested table.
  - `specs/04` status/§4/Frequirements/§5/§6 checkboxes, `specs/00` module map + gap #4, the master
    plan's module state + upload contract + risks, `docs/known-gaps.md`, and `Backend/CLAUDE.md`
    updated in the same change.
- **B2 — SQL generation + execution + user-scoping** — done, test-verified:
  - **`sql_generator.py`** (`app/services/llm/sql_generator.py`): `clean_sql_response()` now
    extracts a single bare SQL statement from plain / fenced (```sql```) / prose-wrapped model
    output by keeping the longest prefix that parses as exactly one statement (text-cleanup only).
    `INVALID_QUERY_SENTINEL` + `is_invalid_query()` (normalized exact match) replace the hardcoded
    sentinel. `build_data_schema(table, columns)` + `build_sql_prompt(query, schema=...)` make the
    prompt schema dynamic; `DEFAULT_DATABASE_SCHEMA` (`sales/customers/orders`) is now only a
    documented fallback until B4 feeds real per-file column metadata.
  - **`app/services/data/executor.py`** (new): `user_data_table_name(user_id)` — deterministic
    per-seuser data table (`user_<uuidhex>_data`), the **co-designed B3 storage contract**;
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
    column).
  - `specs/05` status, §3 contract, §5 edge cases + §6 checkboxes updated in the same change;
    `specs/00` module map + cross-cutting gap #5, `docs/conventions.md` (scoping invariant),
    `docs/known-gaps.md`, `Backend/CLAUDE.md` invariant, and the master plan's module state.
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

## What's after

B4 completed the last **pre-checkpoint** backend phase; the full backend core loop is live and test-verified.
Next: the frontend pre-checkpoint phases F0–F6 (foundations, auth screens, chat workspace, 7 visual
components, composer, upload UI) — now buildable against the **live** `POST /chat`/`/chat/flag`/`/files*`
API instead of mocks. Before any POST-CHECKPOINT phase (B5+ / F7+): **define the "worth continuing" bar**
(e.g. % of first-time users asking a 2nd question in-session) and put the core loop in front of real users
(**🚩 CHECKPOINT**, `specs/00` §7).

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
- **Dynamic schema (B4, resolved):** `get_table_columns()` introspects real, typed per-file columns into
  the `/chat` prompt via `build_data_schema()`; the `sales/customers/orders` placeholder is only a
  documented fallback. B3's per-user tables feed it.
- **Upload storage backend (B3):** local disk for dev (`app/services/data/storage.py`,
  `UPLOAD_DIR`); object store (S3) for prod — module is the swap seam (resolved gap #4).
- **Per-type EXT↔MIME validation (B3):** the "double-check" is enforced per file type (`.csv` →
  `text/csv`, etc.), so a mismatched pair like `.csv`+`application/pdf` is a deliberate `415`
  (`specs/04` §4).
- **One active data file per user (B3):** a fresh upload **replaces** the user's per-user data
  table; `pinecone_namespace` temporarily holds the per-user table name as the storage ref until
  the real Pinecone namespace is wired.
- `.xlsx`/`.pdf` uploads pass validation but land `status="failed"` with a stored reason (parsing
  beyond CSV deferred); raw file is still persisted.
- **7-type visual contract (B4, frozen):** `visual_type` is `Literal["metric","graph","table","comparison",
  "insight","alert","status"]` with `props: Dict`; `src/lib/schemas/visuals.ts` (frontend, F0) is the
  authoritative per-type props source of truth — backend only constrains the type values. `confidence` is
  `Field(ge=0.0, le=1.0)`.
- **LLM never does arithmetic (B4, `specs/11` §2):** `stats.py` computes averages/totals/growth/ratios
  deterministically in pandas; `run_pipeline` receives them as `computed_numbers` to **narrate**, never
  calculate. `GROQ_MODEL` interim = `llama-3.3-70b-versatile`; retires **2026-08-16** → pick a durable model in B5.
- **Trust traceability (B4, `specs/10` §2):** `PipelineOutput.sql_query` + `data_preview` carry the exact SQL
  and raw row slice end-to-end (filled by the route, never the LLM); `QueryLogs` written on every `/chat`;
  `POST /chat/flag` sets `QueryLogs.flagged` (own-only).
- Quota constants (`4` / `6h` / `100`) are a **module decision** in `app/utils/usage.py` (config only
  defines auth rate-limit *counts*); single source of truth for the window rule stays in one place.
- Guest lifetime cap is best-effort (`device_fingerprint`) — accepted tradeoff, `specs/02` §5.
- In-memory per-instance auth limiter is MVP-acceptable; swap to Redis (shared store) with B7.
- Environment gap found: `requests` needed by `google-auth` is not in `requirements.txt` (installed
  only in a `/tmp` temp venv for verification — not modified). Tracked; not part of B1. `pandas`
  **is** now in `requirements.txt` (B3 parser).

## Tests / verification (this run)

`pytest` run from `Backend/` — **149 tests, all green** (temp venv `/tmp/opencode/blvenv`,
Python 3.12; `conftest.py` supplies dummy env vars incl. `GROQ_API_KEY`/`HF_API_KEY` so no `.env`
is needed; no pytest-asyncio — each async scenario runs via `asyncio.run`):

- **B1–B3 modules (unchanged):** auth, quota (incl. `synchronize_session=False` atomic UPDATE now
  exercised by `/chat`), upload validator/parser/files e2e — all still pass.
- **`test_stats.py`** (new): `compute_statistics` — averages/totals/mins/maxs on numeric cols
  (`id` excluded), totals ratios, period-over-period growth %, `<2` periods → no `growth_pct`,
  ISO-string and `datetime` date drivers, NaN/empty/header-only inputs.
- **`test_pipeline_contract.py`** (new): 7 `visual_type`s + `props` (no `chart_data`); bounded
  `confidence`; `clarification` mode; SYSTEM_PROMPT hedged-language + 7-type teaching; 50-row
  truncation with summarizing note; `run_pipeline` fallbacks (bad JSON / empty visuals / exception →
  fallback with `reason`); mutable-default regression.
- **`test_chat_api.py`** (new, file-backed SQLite + seed users/tables, monkeypatched `generate_response`):
  happy `/chat` loop returns `PipelineOutput` with SQL + data_preview; clarification mode;
  `INVALID_QUERY` → graceful fallback still logged; non-`own_data` scope fallback; no-uploaded-data
  response; flag own answer lands on the `QueryLogs` row; flagging another user's log → 404; quota
  429 on window exhaustion and on lifetime cap.
- **Migration check:** `alembic heads` = `b4code0000` (chain `9eec775a77e0 → b1code0000 → b3code0000 → b4code0000`).

## Last updated

2026-08-09 (B4 complete — see `git diff` for the exact change set)
