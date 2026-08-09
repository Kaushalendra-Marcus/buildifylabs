# BuildifyLabs — Implementation Plan (Backend + Frontend)

> **This plan file is the review artifact.** On approval it is split verbatim into two deliverables:
> - `Backend/docs/implementation-plan-backend.md` — everything under **PART B**
> - `Frontend/docs/implementation-plan-frontend.md` — everything under **PART F**
> - The **Shared Contract** section is copied into *both* files (it is the seam between them).
>
> **PLAN ONLY** — no source code, migrations, or config changes are made. The specs in `specs/` are the
> product source of truth; nothing here redesigns or invents requirements.

---

## Context

BuildifyLabs is an **AI Business Intelligence Copilot** — upload business data (CSV/PDF/XLSX) → ask
questions in plain English → get the right visual, a *why*-level root-cause explanation, forecasts /
what-ifs / statistics / benchmarking, recommendations, and optional real-world-news correlation.
Market is **global and industry-agnostic** (`specs/00` §1 — an earlier "India-only SMB" framing was a
corrected wrong assumption; the `Backend/CLAUDE.md` "Indian SMBs" line is stale and should be fixed).

The repo is a partially-built monorepo. **Only Auth and (old-design) quota are wired end-to-end.**
Everything downstream — file upload, NL→SQL execution, the insight pipeline (built but unreachable),
`QueryLogs`, payments, news, graph — is stub/partial/missing. The critical missing piece is a single
`POST /chat` route wiring SQL-gen → execute → pipeline, with user-scoped SQL and trust requirements
built in from the start.

This plan turns `specs/00`'s build order into two dependency-ordered, phase-by-phase implementation
plans, keeping the "put it in front of real users" **checkpoint** (after build step 6) as a hard
boundary between *immediate/MVP scope* and *post-checkpoint* work.

### Scope decisions (confirmed with user)
- **Frontend framework:** build the Chat Workspace fresh on the existing **Vite 8 + React 19 + TS 6**
  scaffold, faithful to `specs/14`. The seven visual components are built new (to `visuals.ts` props).
  `specs/13`'s Next.js migration is treated as a **deferred roadmap** item (it is build-order step 9),
  *not* immediate — this diverges from `specs/00` §2/§4 and `specs/13`, which is flagged as an open item.
- **Detail level:** all 12 build-order steps are detailed, but every phase is tagged **IMMEDIATE (MVP)**
  or **POST-CHECKPOINT (deferred)**. Deferred phases are gated on the real-user checkpoint and must not
  start early ("don't pull deferred features into immediate scope").
- **Auth screens:** included in the immediate frontend plan (they gate the workspace and are the most
  backend-ready surface), even though `specs/14` itself starts at the authenticated workspace.
- **Save location:** `Backend/docs/` and `Frontend/docs/` (per-project).

### Authoritative build order (`specs/00` §7) — the spine of both plans
1. Close auth bugs #1–#2 · 2. Rewrite quota (rolling window + lifetime cap) · 3. Finish `clean_sql_response`
+ `execute_sql()` · 4. Close SQL user-scoping gap · 5. `POST /files/upload` + minimal CSV parse ·
6. First end-to-end `POST /chat` (query→SQL→execute→pipeline) incl. stats (`11` §3.1) + trust (`10` §2)
→ **CHECKPOINT (real users)** → 7. Multi-LLM orchestration (`12`) · 8. Forecasting/what-ifs/benchmarking
(`11` §3.2–3.4) · 9. Frontend migration (`13`) · 10. Integrations + WhatsApp (`09`) · 11. News (`07`) +
graph (`08`) · 12. Payment (`03`, paused).

---

## Shared Contract & Backend↔Frontend Dependencies
*(copied into both deliverable docs)*

The **API seam** is the decoupling mechanism: the frontend builds every domain behind `src/api/*.ts`.
Auth, quota, upload, and chat call the **live** backend today; payments calls **mocks that match the
contracts below** and become a one-file swap when the real routes land. Frontend is therefore **not
blocked** on backend for immediate progress.

### Contract table

| Contract | Backend surface | Frontend consumer | Availability |
|---|---|---|---|
| **Auth** | `POST /auth/{signup,signin,guest,google,refresh}`, `GET /auth/verify-email`, `POST /auth/{forgot,reset}-password` → `AuthResponse{user{id,email,name,plan},access_token,refresh_token,token_type}` (refresh → `TokenResponse`, no `user`) | Auth screens, token store, plan badge | **LIVE now** (after B0 bugfixes) |
| **Quota** | `rate_limiter` dep → `429 {detail}` (window) / `429 {detail, contact_form:true}` (lifetime) | Quota chip, two 429 states | **LIVE after B1** |
| **Contact** | `POST /contact {name,email,message}` → `200 {message}` | Lifetime-cap contact form | B1 |
| **Upload** | `POST /files/upload` (multipart, non-guest) → `202 FileResponse`; `GET /files`, `GET /files/{id}` | Upload popover, file list + status chips | **LIVE after B3** |
| **Chat** | `POST /chat {query, source_scope="own_data", company_name?}` → `PipelineOutput` (+ `POST /chat/flag`) | Message stream, visuals, trust footer, clarification | **LIVE after B4** |
| **Visual types** | `visual_type: Literal["metric","graph","table","comparison","insight","alert","status"]` + `props: Dict` | 7 visual components via plain type→component lookup | contract frozen — **B4** applied it to the pipeline |
| **PipelineOutput** | `answer, visuals[], insights[], summary, root_causes[], recommendations[], news_context[], anomalies[], confidence(0..1), clarification?, sql_query?, data_preview?, query_log_id?` | All assistant-message rendering | schema migrated in **B4** |
| **Payments** | `POST /payments/{create-order,verify,webhook}`, `GET /payments/me` (Razorpay) | Payment/upgrade UI | mocked / **paused** |

### The single most important cross-cutting rule
`src/lib/schemas/visuals.ts` (frontend) is the **single source of truth** for per-`visual_type` `props`
(`specs/06` §3 defers to it explicitly). The backend's `run_pipeline` MUST emit `props` matching it.
**Both sides now agree on the 7 types** — B4 migrated the pipeline to
`Literal["metric","graph","table","comparison","insight","alert","status"]` + `props` + bounded
`confidence` + `clarification` + traceability fields, and `Frontend/docs/type-contracts.md`'s Chat
section was corrected to match (`specs/14` §10 acceptance). **`src/lib/schemas/visuals.ts` itself
landed in F0** — the authoritative per-`visual_type` props source of truth now exists (discriminated
union + runtime guard).

### Sequencing dependencies
- Frontend **F0–F1** (foundations + auth) and quota display depend only on the **live** backend → can
  proceed in parallel with backend B0–B1.
- Frontend **F2–F6** (workspace, message stream, visuals, composer) build against **mocked** `chat`/`files`
  → proceed in parallel; swap to real when **B3/B4** ship.
- The **type contract freeze** (7 types + `PipelineOutput`) is a shared gate: do it once, early, before
  either side writes renderers or the pipeline schema.
- `source_scope` beyond `own_data` (Live web / Both) is backend-**deferred** (needs Pinecone+Redis,
  `specs/07`): the selector is built now, but non-`own_data` scope is gated/mocked until B7.

---
---

# PART B — BACKEND IMPLEMENTATION PLAN
*(→ `Backend/docs/implementation-plan-backend.md`)*

**Stack:** FastAPI (async) + Uvicorn · Neon Postgres + SQLAlchemy async (`asyncpg`) + Alembic ·
`python-jose` JWT + `passlib[bcrypt<4.1]` + `google-auth` · `aiosmtplib` · Groq→HF interim, target
multi-LLM cascade (`12`) · `sqlglot` AST SQL safety. Planned/unwired: Pinecone, Redis, Neo4j, Razorpay.

**Current module state:** Auth ✅ (bugs) · Quota ✅ (B1: rolling window + lifetime cap) · SQL safety ✅
(B2: `clean_sql_response`, `execute_sql`, user-scoping closed; B4 feeds real per-file columns into the
prompt) · Upload ⚠️
(B3: `POST /files/upload` + `GET /files*`, local-disk storage, defensive CSV → per-user table; PDF/XLSX +
Pinecone deferred) · Pipeline ✅
(B4: `run_pipeline` live via `POST /chat`; 7 real types + `props` + bounded `confidence` +
`clarification`; `stats.py` computes deterministic numbers it narrates) · Payments ⚠️ (old UTR,
paused) · `QueryLogs` ✅ written on every `/chat`, flaggable via `POST /chat/flag`.

**Backend conventions to honor throughout** (`Backend/docs/conventions.md`, `CLAUDE.md` §5 — each looks
simplifiable but isn't):
- `app/db/models/__init__.py` imports **every** model.
- Quota rollover logic lives in **exactly one** place (`app/utils/usage.py`).
- Rate limiter check-and-increment is **one** atomic `UPDATE ... WHERE ... RETURNING` (never SELECT+UPDATE).
- All JWTs share one `JWT_SECRET`, distinguished by a `type` claim.
- Auth errors are deliberately **generic** (anti-enumeration).
- SQL safety lives **only** in `sql_sanitizer.py`; `clean_sql_response` is text-cleanup only, never a
  safety decision.
- Plan/quota fail **restrictive** — add the missing log line when unrecognized `plan` is hit.
- After finishing a module, update that spec's status/checkboxes in the same change.

---

## Phase B0 — Auth bugfixes + immediate config fix `[IMMEDIATE]` (build step 1 + `12` one-liner)
**Goal:** stop known crashes/leaks; unblock a reliable LLM path. Small, isolated.

Tasks:
- **`GROQ_MODEL` fix** — default `llama-3.1-70b-versatile` is decommissioned; every call fails 3× then
  silently falls to HF. Set a live model id (interim `llama-3.3-70b-versatile`, itself retiring
  **2026-08-16** — note in a comment). *(`app/config.py`)*
- **Auth bug #1** — `UserCreate.password` is `Optional` but `register_user` unconditionally hashes it →
  passlib throws instead of a clean 400. Make password **required for the email path**.
  *(`app/schemas/user.py`, `app/services/auth/email_auth.py`)*
- **Auth bug #2** — guest lookup with no `device_id` → `device_fingerprint IS NULL` matches many rows →
  `MultipleResultsFound`. Require a non-null `device_id` (or `.first()`). *(`app/services/auth/guest_auth.py`)*
- **Auth bug #8** — `signup`/`signin` wrap the whole body in `except Exception → HTTPException(400, str(e))`,
  masking real 500s and leaking raw exception text. Narrow to the specific `ValueError`s.
  *(`app/routes/auth.py`)*
- **Wire the two auth rate limits** — `LOGIN_RATE_LIMIT`(5) on `/auth/signin`, `VERIFY_EMAIL_RATE_LIMIT`(3)
  on `/auth/verify-email` (both exist in config, unwired). *(`app/middlewares/`, `app/routes/auth.py`)*
- **Spec-01 completeness (decide in/out at execution):** single-use reset tokens; resend-verification
  endpoint. Noted as `specs/01` known-gaps, not strictly on the build-order critical path.

**Files:** `app/config.py`, `app/schemas/user.py`, `app/services/auth/{email_auth,guest_auth}.py`,
`app/routes/auth.py`, `app/middlewares/rate_limiter.py` (or a small auth-specific limiter).
**Depends on:** nothing. **Acceptance:** omitted password → clean 400; no-`device_id` guest → no crash;
DB-down → 500 not 400; 6th rapid signin / 4th verify → rate-limited; Groq calls actually hit Groq.

## Phase B1 — Quota rewrite + contact flow `[IMMEDIATE]` (build step 2, `specs/02`)
**Goal:** replace the old daily-tier/calendar model with rolling-window + lifetime cap. Everything
downstream depends on this being correct.

Tasks:
- **`User` model** — add `questions_in_window`, `window_started_at`, `questions_lifetime`; **remove**
  `queries_today`, `last_reset`. Alembic migration. *(`app/db/models/user.py`, `alembic/versions/`)*
- **`usage.py`** — `reset_daily_usage_if_needed` → `roll_window_if_needed`: rollover when
  `now - window_started_at >= 6h` (reset count to 0, set `window_started_at = now`). Single source of truth,
  UTC-aware. *(`app/utils/usage.py`)*
- **`rate_limiter.py`** — rewrite to enforce **4 questions / rolling 6h** + **100 lifetime**, everyone
  equal (no `plan` branching). Keep the **atomic** `UPDATE ... WHERE ... RETURNING`, now over two counters
  + the roll condition, so a count can't land against a just-rolled window. Emit:
  `429 {detail: "You've used your 4 questions for this 6-hour window. More unlock at <reset_time>."}` and
  `429 {detail: "You've reached the 100-question limit for now.", contact_form: true}`.
  *(`app/middlewares/rate_limiter.py`)*
- **`guest_auth.py`** — drop old `GUEST_DAILY_LIMIT`; guests use the same window logic, tracked by
  `device_fingerprint` (best-effort lifetime cap — accepted). *(`app/services/auth/guest_auth.py`)*
- **`plan_checker.py`** — stays dormant (future paid tier); add the missing log line on unrecognized plan.
- **`POST /contact`** — `{name,email,message}` → email via existing `email_sender.py` async SMTP to new
  `CONTACT_FORM_RECIPIENT_EMAIL`; no email verification (low-stakes lead capture).
  *(new `app/routes/contact.py`, `app/schemas/`, `app/config.py`, `app/main.py`)*
- **Remove all old fields/logic** (no dead code alongside the new — `specs/02` §7 acceptance).

**Files:** `app/db/models/user.py`, `alembic/versions/*`, `app/utils/usage.py`,
`app/middlewares/{rate_limiter,plan_checker}.py`, `app/services/auth/guest_auth.py`,
`app/routes/contact.py`, `app/schemas/*`, `app/config.py`, `app/main.py`.
**Depends on:** B0 (shared files). **Acceptance:** 5th question in window → 429 + reset time; 101st ever →
429 `contact_form:true`; `/contact` sends a real email; concurrent boundary load never exceeds caps; no
path branches on `user.plan` for a quota number; old fields fully gone.

## Phase B2 — SQL generation + execution + user-scoping `[IMMEDIATE]` (build steps 3–4, `specs/05`)
**Goal:** complete a **safe, user-scoped** NL→SQL→rows path. Contains the **blocking security gap**.

Tasks:
- **`clean_sql_response(text)->str`** — strip markdown fences / prose → single bare SQL. Text-cleanup
  **only**; never a safety decision (convention). Handle plain / ```` ```sql ```` / trailing-prose cases.
  *(`app/services/llm/sql_generator.py`)*
- **`execute_sql(query, db)->list[dict]`** — run the sanitized SELECT, respect the enforced `LIMIT`, empty
  result must not fail the pipeline. *(new `app/services/data/executor.py` or `sql_generator.py`)*
- **Reuse `sanitize_sql()`** (done): SELECT-only, no `SELECT *`, mandatory `LIMIT`, ≤1000 chars, AST-walked
  (blocks write/DDL in CTEs/subqueries, forbidden funcs). Do **not** add a second safety check elsewhere.
- **User-scoping (blocking gap #5)** — guarantee a generated query can only read the requester's own data.
  **Chosen approach (tie to B3 storage): execute against a per-user data table/schema** so scoping is
  structural, plus post-generation validation that the query only touches the caller's namespace. (This
  resolves the open "inject `WHERE user_id` vs per-user table" question in favor of per-user tables, which
  also satisfies `specs/08` FR5 later.)
- **Dynamic schema** — replace the hardcoded `sales/customers/orders` `DATABASE_SCHEMA` placeholder with a
  schema generated **per user/file** from B3's ingested tables.
- **`INVALID_QUERY` sentinel** — `SELECT 'INVALID_QUERY' LIMIT 1` short-circuits to a user-facing message,
  never returned as data.

**Files:** `app/services/llm/sql_generator.py`, `app/middlewares/sql_sanitizer.py` (reuse),
`app/services/data/executor.py` (new), `app/db/database.py`.
**Depends on:** B3's storage/table shape (co-design; user-scoping and schema-inference are shared). Note:
build-order lists step 3–4 before step 5, but scoping needs the data-table decision — plan them together.
**Acceptance:** `clean_sql_response` unit-tested (plain/fenced/prose); 100% write/DDL rejection incl.
CTE/subquery; a query can **never** return another user's rows (tested); `INVALID_QUERY` handled gracefully.

## Phase B3 — File upload + minimal ingestion `[IMMEDIATE]` (build step 5, `specs/04`)
**Goal:** persist an uploaded file and land its data in a **queryable per-user table** (skip
Pinecone/embeddings this pass — query the parsed table directly).

Tasks:
- **`POST /files/upload`** (auth, **non-guest**, `validate_file_upload` dep) → **202** `FileResponse`.
  Errors: `403` guest/invalid plan; `415` wrong ext **or** MIME; `413` over size (3MB free / 10MB pro);
  `400` **0-byte** (add explicit rejection). *(new `app/routes/files.py`)*
- **`GET /files`**, **`GET /files/{id}`** (own only; `404` otherwise).
- **Storage backend (resolves gap #4)** — choose and document (local disk for dev / object store for prod);
  persist raw file, then parse. This is a required decision, currently unmade.
- **Minimal parsing (pandas)** — CSV first pass: parse → defensive clean (nulls, type coercion, dedupe,
  encoding, ragged rows) → create a **per-user data table** the SQL layer (B2) queries. PDF/XLSX parsing:
  CSV is the minimum for the checkpoint; XLSX next, PDF later (ties to `specs/08` "tabular first").
- **`FileUpload` model** — add an `error`/`reason` column (edge case 1 requires storing the failure reason);
  status transitions `processing → completed | failed`. Migration.
- Pipeline sets `status="completed"` (namespace/table ref) or `"failed"` (+reason).

**Files:** new `app/routes/files.py`, new `app/services/data/{parser,storage}.py`,
`app/db/models/file_upload.py`, `app/schemas/file_upload.py`, `alembic/versions/*`,
`app/middlewares/file_validator.py` (reuse; add 0-byte check), `app/main.py`.
**Depends on:** B0/B1 (auth+quota); co-designed with B2 (table shape + scoping). **Acceptance:** guest →
403 before bytes; 15MB pro file → 413 no partial write; `.exe` renamed `.csv` → 415; 0-byte → 400; a
processed CSV is queryable via its per-user table.

## Phase B4 — End-to-end `POST /chat` (the critical path) `[IMMEDIATE]` (build step 6; `05`+`06`+`11` §3.1+`10` §2)
**Status: ✅ DONE (37 new tests; full suite 149 passed).** Goal met: the first working demo loop —
query → SQL → execute → stats → pipeline → structured response, with trust requirements built in.

What shipped:
- **`langchain_pipeline.py` migrated to the `specs/06` §3 contract:** 7 real `visual_type`s +
  `props: Dict` (no `chart_data`); `confidence` `Field(ge=0.0, le=1.0)`; `clarification` alternate
  mode; `run_pipeline` takes `source_scope` (no `include_news`), `news_context: list | None = None`
  (no mutable default); `SYSTEM_PROMPT` teaches the 7 real types + hedged causal language;
  `_truncate_rows` caps prompt rows at 50 (edge case 6).
- **`POST /chat`** (`app/routes/chat.py`): `rate_limiter` → build SQL prompt from the user's **real**
  uploaded columns (`get_table_columns` → `build_data_schema`) → LLM → `clean_sql_response` →
  `sanitize_sql` → user-scoped `execute_sql` → **pandas stats (`11` §3.1)** → `run_pipeline` →
  response. `INVALID_QUERY` → graceful message. `source_scope` is `own_data` only (B7 deferred).
- **Trust requirements (`10` §2):** response carries `sql_query` + `data_preview` (real "show the
  query"); hedged causal language enforced; **`QueryLogs` written on every request+response**
  (incl. fallbacks); **`POST /chat/flag`** sets `QueryLogs.flagged` (own-only); `clarification` is a
  working output mode.
- **Migration `b4code0000`** adds `QueryLogs.flagged`.

**Files:** `app/routes/chat.py`, `app/services/llm/langchain_pipeline.py` (migrated),
`app/services/data/stats.py`, `app/services/data/executor.py::get_table_columns`,
`app/db/models/query_logs.py` (flagged), `app/schemas/chat.py`, `app/main.py`,
`alembic/versions/b4code0000_query_logs_flag.py`; tests `tests/test_stats.py`,
`tests/test_pipeline_contract.py`, `tests/test_chat_api.py`.
**Depends on:** B2 (SQL exec + scoping), B3 (data), **type-contract freeze** (7 types). **Acceptance
(`06` §6 + `10` §5):** end-to-end route returns valid `PipelineOutput`; `visual_type`/`confidence`
schema-constrained; `clarification` exercised by ≥1 real path; every answer traceable to SQL;
`QueryLogs` written per query; 10k+ rows don't blow context. **All met and covered by tests.**
**Noted during build:** `rate_limiter`'s atomic UPDATE needed `synchronize_session=False` — its
ORM evaluate path compared a SQLite-loaded naive `window_started_at` against the tz-aware `now`
(crashed on commit once `/chat` started hitting it; SQL unchanged).

> ### 🚩 CHECKPOINT — put this in front of real users before continuing.
> Steps 7+ below are **priority-ordered, not committed scope** (`specs/00` §7, `09` §8). **Define the
> "worth continuing" bar before this point** (e.g. % of first-time users asking a 2nd question in-session).
> Do **not** begin any POST-CHECKPOINT phase until the core loop has real-user evidence.

## Phase B5 — Multi-LLM orchestration `[POST-CHECKPOINT]` (build step 7, `specs/12`)
- `generate_completion(prompt, system_prompt, *, task: Literal["default","sql","synthesis"]="default")
  -> {"content", "source"}`: `default` → cascade **Groq→Gemini→open-source**; `sql` → cascade, retry
  once on **Claude** if validation/execution fails; `synthesis` → **Claude** directly. Per-provider client
  wrappers behind one interface; per-provider failure/cool-down tracking. Config:
  `GROQ_API_KEY,GEMINI_API_KEY,<open-source>,ANTHROPIC_API_KEY`. Migrate `generate_response` call sites.
  Cascade-failed gets its own log line; `source` observable in logs.

## Phase B6 — Forecasting, what-ifs, benchmarking `[POST-CHECKPOINT]` (build step 8, `specs/11` §3.2–3.4)
- Trend forecasting (linear regression / moving average; always surface **method + confidence range**).
- What-if scenarios (parameterized recompute; **state assumptions**; v1: quantity unaffected by price).
- Competitor benchmarking (first real **ask-don't-guess** use: clarification offering search / industry-avg
  / user-provided). All arithmetic in code; LLM narrates. Extends `/chat`, no new endpoint.

## Phase B7 — News context + live `source_scope` `[POST-CHECKPOINT/DEFERRED]` (build step 11, `specs/07`)
- Provision **Pinecone + Redis** (make required). `live_web`/`both`: category web scraping (headline+snippet,
  robots/ToS-respecting), **Redis TTL cache (~6h)**. Clarification-on-disagreement (selector vs free text).
  Feeds `news_context` into `run_pipeline`. Deferred until the core loop is validated (`09` §8).

## Phase B8 — Graph knowledge store `[POST-CHECKPOINT/DEFERRED]` (build step 11, `specs/08`)
- Neo4j (AuraDB free). `build_graph_from_upload(...)`, `graph_query_for_intent(...)`. **Per-user isolation
  (FR5, non-negotiable).** Dual write (Pinecone chunks + Neo4j relationships); hybrid retrieval; **graph
  facts win on conflict**; cleanup **both** stores on file delete. Tabular first, PDF NER later; async
  ingestion. Config `NEO4J_URI/USER/PASSWORD`.

## Phase B9 — Payments (Razorpay) `[POST-CHECKPOINT/PAUSED]` (build step 12, `specs/03`)
- Revise `Payment`: remove `utr`; add `razorpay_order_id` (unique, indexed), `razorpay_payment_id`,
  `razorpay_signature`; status `created|paid|failed`. Endpoints: `POST /payments/create-order`
  (201, amount in **paise** 29900, conversion in one place), `/verify` (signature + ownership), `/webhook`
  (**source of truth**, signature-verified, **idempotent** upgrade), `GET /payments/me`. Config
  `RAZORPAY_KEY_ID/SECRET/WEBHOOK_SECRET`. Admin role + refunds out of scope. **Paused** — build only when
  usage justifies it.

## Backend — cross-cutting concerns
- **Security:** SQL user-scoping (blocking, B2); anti-enumeration auth errors; JWT single-secret+type; AST
  SQL safety (no write/DDL); webhook signature verification (B9); repo-wide secret scan (a Tambo key was
  committed client-side — rotation/history-scrub is a git task, see frontend/roadmap).
- **Reliability:** atomic quota; multi-LLM cascade + cool-down (B5); graceful pipeline fallback
  (`confidence=0.0`); Render cold start ~30–40s; `db_data` truncation.
- **Compliance (`specs/10` §4), before any non-test user data:** privacy policy + ToS + retention/deletion
  mechanism (`FileUpload`, `QueryLogs`); explicit **no-training** commitment across **every** provider in
  the cascade; data-processor awareness; breach-response runbook.

## Backend — testing strategy
- **Suite live** (`Backend/tests/`, run `python -m pytest` from `Backend/`, no pytest-asyncio —
  each async scenario is driven with `asyncio.run`): 112 tests green across B1–B3.
- **Unit:** `clean_sql_response` (plain/fenced/prose); `sanitize_sql` regression (100% write/DDL incl.
  CTE/subquery); quota window rule; validator (403/415/413/400); parser/cleaning (encoding, ragged,
  dedup, date/₹-coercion, typed columns).
- **Integration:** auth flows; quota (5th→429, 101st→`contact_form`); **user-scoping** (never another
  user's rows); upload routes end-to-end (403/413/415/400, 202, per-user table queryable,
  `GET /files*` ownership, xlsx/pdf → `failed` with stored reason); `/chat` end-to-end (B4).
- **Load:** concurrent at quota boundaries never exceed caps.

## Backend — definition of done (immediate/MVP)
Build steps 1–6 complete and tested; a user can sign up → upload a CSV → ask a question → receive a
trustworthy, traceable `PipelineOutput` with inline visuals; quota enforced (rolling + lifetime) with the
contact flow; every answer logged to `QueryLogs`; SQL provably user-scoped; each finished module's spec
status/checkboxes updated in the same change.

## Backend — risks & unresolved questions
1. **~~Storage backend~~ — decided in B3**: local disk for dev (`app/services/data/storage.py`,
   `UPLOAD_DIR`), object store (S3) for prod; the module is the swap seam.
2. **User-scoping mechanism** — plan chooses per-user tables/schema; confirm it fits Neon + Alembic and
   later Neo4j scoping.
3. **Where parsed data lands** — per-user tables in the same Neon DB (schema-per-user vs table-per-file);
   affects B2 scoping, sanitizer, and dynamic-schema generation.
4. **Live `GROQ_MODEL` id** — interim `llama-3.3-70b-versatile` retires 2026-08-16; needs a durable choice.
5. **~~Dynamic schema generation~~ — resolved in B4**: `get_table_columns()` introspects the real
   per-file columns (information_schema → PRAGMA fallback) and feeds `build_data_schema()` into the
   `/chat` prompt; the `sales`/`customers`/`orders` placeholder is now only a documented fallback.
6. Stale docs: ~~`Backend/CLAUDE.md`~~ updated for B4; empty `docker-compose.yml` (no container workflow assumed).

---
---

# PART F — FRONTEND IMPLEMENTATION PLAN
*(→ `Frontend/docs/implementation-plan-frontend.md`)*

**Base:** unmodified **Vite 8 + React 19.2 + TS 6 + ESLint 9** scaffold (`src/App.tsx` is boilerplate,
safe to replace). Nothing else chosen yet — router, state, CSS, HTTP client, charts, icons, OAuth are
**deliberate per-screen decisions**, then held consistent.
**Design source of truth:** `specs/14` (Chat Workspace) + `Frontend/docs/type-contracts.md` (corrected) +
the approved Figma "Buildify Labs — Chat Workspace". **Do not redesign** — implement the existing design
faithfully. Figma Dev Mode MCP was unreachable when `specs/14` was written; **reconcile tokens/frames
against Figma once accessible** (build to `specs/14`'s token *roles* meanwhile).

**Recommended library choices** (to be confirmed at F0; chosen to match `specs/13`'s library decisions and
`specs/14`'s "no hand-rolled / no generative-UI SaaS" spirit): **react-router** · **Zustand**
(`chat-store.ts`, consistent with spec 13 naming) · a thin **fetch** wrapper as the HTTP client ·
**Recharts** (charts) · **lucide-react** (icons) · **Google Identity Services** (Google sign-in) ·
**Vitest + React Testing Library** (tests). CSS approach chosen at F0 and applied consistently.

**Buildable vs mocked (`Frontend/CLAUDE.md` §3):** Auth + quota + Upload + Chat → **live** (upload B3,
chat B4); Payments → **mock** (paused).

**Structure (`Frontend/docs/structure.md`):**
```
src/
  api/         auth.ts · files.ts · chat.ts · payments.ts · contact.ts   (mock/real seam — one-file swap)
  types/       interfaces mirroring docs/type-contracts.md
  lib/schemas/ visuals.ts   (SINGLE SOURCE OF TRUTH for per-visual_type props)
  components/  shared UI + one wrapper per visual_type
  features/    auth/ · chat/ · upload/ · payments/ · dashboard/
  hooks/       useAuth · useQuota · useTokenRefresh
```
**Env (`VITE_`-prefixed only; never a real secret):** `VITE_API_BASE_URL`, `VITE_GOOGLE_CLIENT_ID` (match
backend `GOOGLE_CLIENT_ID`), `VITE_RAZORPAY_KEY_ID` (public key, later). **Dev server stays on port 5173**
(backend CORS is locked to it).

---

## Phase F0 — Foundations + type-contract freeze `[IMMEDIATE]`
**Status: ✅ DONE (2026-08-09; frontend `build`/`lint`/Vitest green, 6 tests).** Stack decided once
(react-router · Zustand · thin fetch wrapper · plain-CSS design tokens · Recharts · lucide-react ·
GIS · Vitest/RTL); structure `src/{api,types,lib/schemas,components,features,hooks}` created;
boilerplate replaced; `specs/14` §7 token roles implemented with placeholders (Figma
reconciliation pending); `src/lib/schemas/visuals.ts` landed as the per-`visual_type` props source
of truth; auth/upload/chat/payment/contact types mirrored in `src/types/`; `src/api/*.ts` seam
(live auth/files/chat/contact, mocked payments); token-storage decision + `useAuth`,
`useTokenRefresh`, `useQuota` hooks; `docs/type-contracts.md`, `docs/structure.md`, `CLAUDE.md`,
`index.html` updated in the same change.
**Goal:** make deliberate stack choices once, stand up the structure + API seam + design tokens, and
**freeze the shared type contract** before any renderer is written.

Tasks:
- **Install + decide:** router, Zustand, fetch wrapper, CSS approach, Recharts, lucide-react, Google
  Identity Services, Vitest/RTL. Keep port 5173.
- **Structure:** create `src/{api,types,lib/schemas,components,features,hooks}`; replace boilerplate
  `App.tsx`/`App.css`/`index.css`.
- **Design tokens (`specs/14` §7 roles):** `surface-page|card|raised`, `text-primary|secondary|muted`,
  `accent`, `success|danger|warning`, type scale **13px captions / 15px body / 20–24px metric numbers**,
  **nothing below 12px**. Exact hex/spacing are a follow-up (`specs/14`) → implement a token **system** with
  placeholders, flagged for **Figma reconciliation**. (The scaffold's `#aa3bff` purple / 18px base are
  template boilerplate, **not** product tokens.)
- **Type layer + contract freeze (shared gate):** correct `docs/type-contracts.md`'s Chat section to the
  **7 `visual_type` values + `props` + bounded `confidence` + `clarification`**; create
  `src/lib/schemas/visuals.ts` as the per-type props source of truth (`specs/06`/`14` acceptance:
  **before** any renderer). Mirror auth/upload/chat/payment types.
- **API seam:** `api/*.ts` with mock implementations behind the real contract signatures (e.g.
  `chat.sendQuery()`), so components never know mock vs real.
- **Auth/token plumbing:** token storage decision (backend expects a **Bearer header** — cookie storage
  would need a backend change; recommend in-memory access token + stored refresh, or localStorage for MVP);
  `useAuth`, `useTokenRefresh` (access 60 min / refresh 7 d), `useQuota`.

**Depends on:** nothing (parallel with backend B0–B1). **Acceptance:** `type-contracts.md` Chat section is
the 7 real types before F4; API seam swappable in one file; tokens resolve `specs/14` §7 roles.

## Phase F1 — Auth screens `[IMMEDIATE]` (`specs/01`, live backend)
- Screens: **Signup, Signin, Google (GIS), Guest (`device_id`), Verify-email** (`GET /auth/verify-email?token`),
  **Forgot-password, Reset-password** (min 8 chars).
- `AuthResponse` handling (user + access + refresh); store tokens; render **plan badge**
  (`guest|free|pro`).
- **Display generic auth errors as-is** (anti-enumeration — never re-word client-side).
- Route guards → authenticated workspace. Styled with F0 tokens.
**Depends on:** F0. **Acceptance:** all six flows work against the live API; guarded routes; generic errors
shown verbatim.

## Phase F2 — Chat Workspace shell `[IMMEDIATE]` (`specs/14` §3)
- Three regions, **one layout, no separate drawer/workspace surface**: **Header (56px)** — logo, quota chip,
  plan badge, account menu, "new chat"; **Chat history rail** (0 / 280px, **collapsed by default <768px**,
  **overlay** — doesn't push content); **Message stream** (the only place visuals render); **Composer**
  (fixed to bottom of the stream column).
- **No resizable panel, no fullscreen affordance at all** (constraint).
**Depends on:** F0. **Acceptance:** matches `specs/14` §3 layout; rail overlays on narrow viewports.

## Phase F3 — Message stream components `[IMMEDIATE]` (`specs/14` §4)
- **User message (4.1):** right-aligned bubble; uploaded-file chip **above** the bubble.
- **Assistant normal answer (4.2):** left-aligned, **no bubble**: `answer` prose → **visual cards grid**
  (`repeat(auto-fit, minmax(240px, 1fr))`; `graph` + `table` span 2 cols) → **insights strip** (collapsed
  by default: `insights`/`root_causes`/`recommendations`, labelled **"Possible factors"** — never "Why this
  happened", a `specs/10` §2 content rule) → **trust footer** (always visible: **Show the query** |
  **confidence meter** (render only once value is in 0..1) | **Flag this answer** (disable with a tooltip if
  the write path isn't live — don't hide)) → **news-context row** (only when non-empty, "from the web").
- **Clarification (4.3):** accent left edge, `question`, `options[]` as **pill buttons**; tap **sends it
  verbatim** as the next user message; **no** answer/cards/footer.
- **Fallback/low-confidence (4.4):** distinct neutral notice "Couldn't produce a reliable answer for that".
**Depends on:** F0 (contract), F2. **Acceptance:** the four message types render per `specs/14` §4; hedged
labels; trust footer on every non-fallback/non-clarification answer.

## Phase F4 — Seven visual components `[IMMEDIATE]` (`specs/14` §2.11, `06` FR3, `visuals.ts`)
- **MetricCard**(`metric`), **GraphCard**(`graph` — Recharts line/bar/pie/area), **BusinessSummaryTable**(`table`),
  **ComparisonCard**(`comparison`), **InsightCard**(`insight`), **AlertList**(`alert`), **StatusBadge**(`status`).
- **Plain type→component lookup** (no interception layer); **fallback component** for an unrecognized type
  (defensive — backend enum not yet guaranteed at runtime). Built to `visuals.ts` props; lucide-react icons.
**Depends on:** F0 (`visuals.ts`), F3. **Acceptance:** each of the 7 types renders inline in its message;
unknown type degrades gracefully.

## Phase F5 — Composer + ambient controls `[IMMEDIATE]` (`specs/14` §5)
- **Text input (5.1):** multiline auto-grow; placeholder **"Why did revenue drop last week?"**.
- **Source-scope selector (5.2):** 3-way segmented **Your data / Live web / Both**; always visible; default
  **Your data**; persists across queries; **clarification on disagreement** (never switch silently). *Live
  web / Both are backend-deferred (B7) — selector is built now; non-`own_data` is gated/mocked until then.*
- **Upload (5.3):** icon button **visible only when `plan !== "guest"`** (absent, not disabled, for guests);
  popover with drag-drop, "CSV, PDF, or XLSX", size hint **3MB free / 10MB pro**; file list with status chip
  (`processing`/`completed`/`failed`); `failed` shows the stored reason once that server field exists.
  Mocked via `api/files.ts`.
- **Send (5.4):** disabled **only when input is empty** — never for exhausted quota.
- **Quota chip (5.5):** always visible, e.g. **"3 of 4 left · resets in 4h"** from the rolling window.
- **Two 429 states (5.6):** **window-exhausted** → inline notice with reset time, input **stays enabled**;
  **lifetime cap** (`contact_form:true`) → distinct **permanent-feeling card** (not a toast) with the inline
  **contact form** (name/email/message → `POST /contact`).
- **Cold start (5.7):** named state **"Waking up the server — first load can take up to a minute"**,
  determinate-feeling, **first request of a session only** — distinct from the per-message thinking indicator.
**Depends on:** F2, quota live (B1). **Acceptance:** `specs/14` §5 behaviors; two distinct 429 states;
upload absent for guests; send never blocked by quota.

## Phase F6 — Remaining states `[IMMEDIATE]` (`specs/14` §6)
- **Empty thread, guest:** invite a question, **no upload affordance**.
- **Empty thread, registered, no files:** invite an upload ("Add a CSV, PDF, or spreadsheet to get
  started"); a no-data question routes through **no-data messaging** (`07` edge case 2), not a generic empty.
- **Assistant "thinking":** small inline indicator under the user's message (distinct from cold start).
**Depends on:** F2–F5. **Acceptance:** all `specs/14` §6 states present.

## Phase F7 — Payments / upgrade UI `[POST-CHECKPOINT/PAUSED]` (`specs/03`)
- Mock `api/payments.ts`; Razorpay Checkout flow (create-order → checkout with `key_id`+`order_id` →
  `/verify` as UX-speed only → refetch `GET /payments/me`). **Do not build toward payment yet** (`09` §7,
  paused). Detailed for completeness.

## Phase F8 — Live source-scope / news row `[POST-CHECKPOINT/DEFERRED]` (`specs/07`)
- Enable Live web / Both once B7 ships; the news-context row is already built (F3).

## Phase F9 — Deferred roadmap `[POST-CHECKPOINT/DEFERRED]`
- **`specs/13` Next.js migration (alternate path):** because Vite-fresh was chosen, spec 13's migration is
  the deferred alternative — if the org later adopts the sibling Next.js app, execute: move app, remove
  Tambo, **rotate + git-scrub the exposed Tambo key**, add Recharts/lucide, keep the 7 components. Document,
  don't execute now.
- **Integrations (`09` §3):** Google Sheets → Shopify/WooCommerce → QuickBooks/Xero (connect-account flows).
- **WhatsApp interface (`09` §4)**, **weekly digest (`09` §5)**, **TanStack Table for BusinessSummaryTable
  (`13` §2.5)** — all future.

## Frontend — cross-cutting concerns
- **Trust UX (`specs/10` §2):** show-the-query, confidence meter (gated on 0..1), flag, hedged copy — first
  class, never behind settings.
- **Global/industry-agnostic copy (`09` §2):** no India-only or vertical framing.
- **Responsive + cold-start** everywhere; **accessibility** (labels, focus, contrast on both themes).
- **Figma reconciliation (`specs/14` note):** reconcile tokens/frames against the approved Figma once Dev
  Mode MCP is available.

## Frontend — testing strategy
- **Vitest + React Testing Library.** Component tests: the four message types; visual type→component lookup
  (incl. unknown-type fallback); quota chip; **two distinct 429 states**; **upload absent for guests**;
  **source-scope default + persistence**; clarification "tap sends verbatim". Use `specs/14` §10 as the
  graded acceptance checklist.

## Frontend — definition of done (immediate/MVP)
The Chat Workspace matches `specs/14` and its §10 acceptance checklist; auth (six flows) works against the
live backend; quota chip + both 429 states + contact form work against the live backend; upload + chat run
against mocks behind the API seam and are a one-file swap when B3/B4 land; the 7 visual components render
inline via a plain lookup; `type-contracts.md` Chat section is the 7 real types.

## Frontend — risks & unresolved questions
1. **Framework divergence:** Vite-fresh (chosen) contradicts `specs/00` §2/§4 and `specs/13` ("Next.js").
   Recommend updating those specs (or adding a decision note) so the docs don't stay out of sync.
2. **`type-contracts.md` Chat section is stale** (9-type `chart_data`) — must be corrected first (F0 gate).
3. **`structure.md` says "resets at UTC midnight"** — stale (old quota model); update to the rolling window.
4. **Design tokens exact values undefined** (`specs/14` §7) + **Figma not yet reconciled** → placeholder
   tokens now, reconcile later.
5. **`specs/14` §9 open questions:** history-rail grouping (transcript vs day/week); per-chart-type
   treatment for `graph`; account-menu contents. Non-blocking for a first build.
6. **Per-`visual_type` `props` shapes** not yet pinned — `visuals.ts` must define them (coordinate with B4).
7. **Token storage** (localStorage vs cookie) — backend expects Bearer; cookie needs a backend change.
8. **CSS approach** choice (F0) — commit once, stay consistent.

---
---

## Pre-save review (self-check against the specs)
Before writing the two files I will verify: (a) nothing is pulled ahead of the `specs/00` checkpoint into
immediate scope (every deferred phase is tagged and gated); (b) no invented requirements — every task
traces to a spec section; (c) no duplication between the two plans except the intentionally-shared Contract
section; (d) the type-contract freeze appears as a shared gate in both plans; (e) build order matches
`specs/00` §7 exactly.

## Verification (after implementation — for reference, not part of PLAN ONLY)
- **Backend:** `alembic upgrade head`; `uvicorn app.main:app --reload`; exercise `/auth/*`, quota
  (5th→429, 101st→`contact_form`), `/contact`, `/files/upload` (403/413/415/400), `/chat` end-to-end;
  `pytest` unit + integration + boundary load.
- **Frontend:** `npm install && npm run dev` (port 5173); auth six flows against live backend; quota chip +
  both 429 states; workspace/message-stream/visuals against mocks; `npm run lint`, `npm run build`, Vitest;
  walk `specs/14` §10 checklist.
- **Contract:** confirm backend `PipelineOutput.props` matches `src/lib/schemas/visuals.ts` for all 7 types.

## Deliverables
- `Backend/docs/implementation-plan-backend.md` — PART B + Shared Contract.
- `Frontend/docs/implementation-plan-frontend.md` — PART F + Shared Contract.
