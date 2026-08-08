# BuildifyLabs — STATUS

Tracks one-task-at-a-time progress against
[`implementation-plan-master.md`](./implementation-plan-master.md). Source of truth for product
requirements is `specs/`; re-read only the relevant plan/phase before each run.

Legend: ✅ completed · ⚠️ partial · ⛔ blocked · ⏸ deferred/paused

## Current task

**Phase B1 — Quota rewrite + contact flow** `[IMMEDIATE]` (build step 2, `specs/02`)
— ✅ **complete (this run).** Rolling 6h window + lifetime cap live; old daily-tier design
fully removed; `POST /contact` wired.

## Completed tasks

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
  - `specs/02` status + §7 acceptance checkboxes updated in the same change; `docs/conventions.md`,
    `docs/known-gaps.md`, `Backend/CLAUDE.md` invariant updated to the new model.

## Next task

**Phase B2 — SQL generation + execution + user-scoping** `[IMMEDIATE]` (build steps 3–4, `specs/05`):
`clean_sql_response()` (plain/fenced/prose), `INVALID_QUERY` sentinel, `execute_sql()`, and the
**blocking user-scoping gap** — per-user data tables/schema (co-designed with B3 storage), so a query
can never read another user's rows.

## Blocked / deferred

- **Spec-01 completeness (single-use reset tokens; resend-verification endpoint)** — ⏸ decided OUT
  of B0 at execution (plan: "decide in/out at execution"; known-gaps, not on critical path).
- **Phase B9 payments / F7 upgrade UI** — ⏸ paused (`specs/03`).
- **Phases B5–B8, F7–F9** — 🔴 post-checkpoint (`specs/00` §7); do not start before real-user checkpoint.
- **B4 → frontend F8 live source-scope** — 🔴 gated on check B7.
- **B7 `source_scope` beyond `own_data`** — needs Pinecone+Redis (`specs/07`).

## Important decisions

- `GROQ_MODEL` interim = `llama-3.3-70b-versatile`; retires **2026-08-16** → pick a durable model in B5.
- Quota constants (`4` / `6h` / `100`) are a **module decision** in `app/utils/usage.py` (config only
  defines auth rate-limit *counts*); single source of truth for the window rule stays in one place.
- Guest lifetime cap is best-effort (`device_fingerprint`) — accepted tradeoff, `specs/02` §5.
- In-memory per-instance auth limiter is MVP-acceptable; swap to Redis (shared store) with B7.
- Environment gap found: `requests` needed by `google-auth` is not in `requirements.txt` (installed
  only in a `/tmp` temp venv for verification — not modified). Tracked; not part of B1.

## Tests / verification (this run)

No pytest suite exists yet (established in the backend test-strategy phase). B1 was smoke-verified in
a temp venv (`/tmp/opencode/blvenv`, Python 3.12 + `aiosqlite`):

- `import app.main` / all routes / models — OK.
- Model columns: old fields gone, three new fields present.
- Rendered atomic UPDATE (postgres dialect) matches the intended two-counter + roll shape.
- Behavioral, in-memory SQLite exec of the **same** UPDATE expression: fresh window allows 4 then
  5th denied; backdated window rolls (allowed, count resets to 1); lifetime at 99 → allowed (100),
  then denied at 100.
- E2E (TestClient): `POST /contact` → 200 with exact body (SMTP patched); invalid email → 422;
  `QuotaLimitExceeded` handler produces `429` with exact bodies incl. top-level `contact_form: true`.
- Frontend still scaffold-only; mixing not exercised.

## Last updated

2026-08-09 (B1 complete — see `git diff` for the exact change set)