# BuildifyLabs — STATUS

Tracks one-task-at-a-time progress against
[`implementation-plan-master.md`](./implementation-plan-master.md). Source of truth for product
requirements is `specs/`; re-read only the relevant plan/phase before each run.

Legend: ✅ completed · ⚠️ partial · ⛔ blocked · ⏸ deferred/paused

## Current task

**Phase B0 — Auth bugfixes + immediate config fix** `[IMMEDIATE]` (build step 1 + `12` one-liner)
— ✅ **complete (this run).** Groq model id fixed; two crash bugs fixed; exception masking fixed;
auth rate limits wired.

## Completed tasks

- **B0 — Auth bugfixes + immediate config fix (build step 1)** — done, smoke-verified:
  - `GROQ_MODEL` → `llama-3.3-70b-versatile` (default `llama-3.1-70b-versatile` was decommissioned).
    Interim model retirement 2026-08-16 noted (needs durable choice, plan B5). `app/config.py`.
  - Auth bug #1: `UserCreate.password` now **required** → omitted/weak password = clean 422, no
    passlib crash. `app/schemas/user.py`.
  - Auth bug #2: `device_id` now **required** in `GuestAuthRequest` (+ defensive guard in
    `create_guest_user`) → no `MultipleResultsFound`. `app/schemas/auth.py`, `guest_auth.py`.
  - Auth bug #8: `signup`/`signin` `except Exception` narrowed to `except ValueError` → DB-down /
    bugs surface as 500, not 400 with leaked text. `app/routes/auth.py`.
  - Wired auth rate limits via new `app/middlewares/auth_rate_limiter.py`: `LOGIN_RATE_LIMIT`(5) on
    `/auth/signin` (key: IP+email), `VERIFY_EMAIL_RATE_LIMIT`(3) on `/auth/verify-email` (key: IP),
    1h rolling window, in-memory per-instance (Redis is deferred to B7).
  - `specs/01-authentication.md` edge cases 1, 2, 7, 8 + acceptance checkboxes updated in same change.

## Next task

**Phase B1 — Quota rewrite + contact flow** `[IMMEDIATE]` (build step 2, `specs/02`): rolling
6h-window (4 questions) + lifetime cap (100) on `User`; single atomic `UPDATE ... WHERE ...
RETURNING` over both counters + roll condition; drop old daily-tier fields/logic;
guests via `device_fingerprint`; `POST /contact`; `app/utils/usage.py` single source of truth.

## Blocked / deferred

- **Spec-01 completeness (single-use reset tokens; resend-verification endpoint)** — ⏸ decided OUT
  of B0 at execution (plan: "decide in/out at execution"; known-gaps, not on critical path).
- **Phase B9 payments / F7 upgrade UI** — ⏸ paused (`specs/03`).
- **Phases B5–B8, F7–F9** — ⛔ post-checkpoint (`specs/00` §7); do not start before real-user checkpoint.
- **B4 → frontend F8 live source-scope** — ⛔ gated on check B7.

## Important decisions

- `GROQ_MODEL` interim = `llama-3.3-70b-versatile`; retires **2026-08-16** → pick a durable model in B5.
- Auth rate-limit **window** is a module decision (config only defines counts): 1h rolling window.
- In-memory per-instance rate limiter is MVP-acceptable; swap to Redis (shared store) with B7.
- `reset_daily_usage_if_needed` and old guest daily limit are untouched in B0 — the quota rewrite
  (B1) owns the migration to the rolling window + lifetime cap.
- Environment gap found: `requests` needed by `google-auth` is not in `requirements.txt`
  (installed only in a `/tmp` temp venv for verification — not modified). Tracked; not part of B0.

## Tests / verification (this run)

No pytest suite exists yet (established in the backend test-strategy phase). B0 was smoke-verified in
a temp venv (`/tmp/opencode/blvenv`, Python 3.12):

- `import app.main` / `app.routes.auth` / `auth_rate_limiter` — OK (needs `requests` installed).
- Rate limiter standalone test (TestClient): 5th login of a key → 429; different email = fresh key;
  4th verify → 429. Confirmed the dependency calling `request.json()` does not break the endpoint's
  own body parsing.
- Schema tests: signup w/o password → 422; weak password → 422; guest w/o `device_id` → 422.
- Bug#8 test (DB-unreachable): signup/signin → 500 (not 400), no leaked connection text.

## Last updated

2026-08-09 (B0 complete — see `git diff` for the exact change set)