# Known gaps — quick index

Compiled across all spec files for fast scanning. Each entry is one line — if you're actually
fixing one, open the cited spec file for the full edge-case writeup and acceptance criteria before
starting; don't fix from this summary alone.

## Auth (`specs/01-authentication.md`)

- `UserCreate.password` is `Optional` but `register_user()` assumes it's set — omitting it throws
  inside `passlib` instead of a clean 400.
- Guest lookup with no `device_id` can raise `MultipleResultsFound` (`device_fingerprint IS NULL`
  legally matches multiple rows).
- No rate limiting on `/auth/signin` or `/auth/verify-email` — both brute-forceable, despite
  `LOGIN_RATE_LIMIT`/`VERIFY_EMAIL_RATE_LIMIT` already existing in config.
- Password reset tokens are time-limited (20 min) but not single-use — a leaked link stays valid
  for the rest of its window even after a successful reset.
- No resend-verification-email endpoint — a user with a lost/expired link has no self-serve way back in.

## Plan/quota (`specs/02-plan-quota-enforcement.md`)

- `GUEST_DAILY_LIMIT` (2) is defined in two places (`guest_auth.py` and `rate_limiter.py`) — they
  agree today but aren't centralized, so a future change to one without the other silently desyncs them.
- Unrecognized `plan` values fail safe but silently (no log line) — see `docs/conventions.md`.

## Payments (`specs/03-payment-verification.md`)

- **Model/schema still reflect the superseded UTR/manual-UPI design** — `specs/03` already reversed
  the decision to Razorpay (order + webhook), but `app/db/models/payment.py` and
  `app/schemas/payment.py` haven't been migrated. Don't build routes against the current shape.

## File upload (`specs/04-file-upload-ingestion.md`)

- No storage backend chosen — validation can pass with nowhere to persist the file afterward.
- Size check loads the whole file into memory before checking size — fine at today's ≤10MB cap,
  won't scale if caps are raised without moving to streaming checks.
- Empty (0-byte) uploads currently pass validation and need explicit rejection.

## NL→SQL (`specs/05-query-sql-safety.md`)

- **[Blocking]** No automatic user-scoping on generated SQL — nothing forces a generated query to
  filter to the requesting user's own data. Must be closed before any multi-tenant use.
- `sql_generator.clean_sql_response()` is an empty stub — fenced/prose-wrapped LLM output isn't
  stripped before hitting the sanitizer.
- `DATABASE_SCHEMA` in `sql_generator.py` is a placeholder (`sales`/`customers`/`orders`) that
  doesn't match this app's real tables — must become dynamic per user/file.

## AI insight pipeline (`specs/06-ai-insight-pipeline.md`)

- `VisualOutput.visual_type` (`str`) and `PipelineOutput.confidence` (`float`) are unconstrained —
  should be `Literal[...]` and `Field(ge=0.0, le=1.0)`; an invalid LLM value currently passes
  validation and reaches the caller as-is.
- No truncation/summarization step before large `db_data` is injected into the prompt — risk of
  blowing the model's context window on the input side.
- No route calls `run_pipeline` yet — it's fully built but unreachable.

## Cross-cutting

- No admin role exists on `User` at all — relevant once payments need manual override/refund support.
- `QueryLogs` (`app/db/models/query_logs.py`) exists but nothing writes to it — once `/chat` ships,
  log every query+response pair. This is the cheapest way to actually know what people ask and
  where the model fails, instead of guessing. See `../../specs/10-trust-safety-compliance.md` §2.
- No product-facing trust affordances exist yet for AI-generated answers (show-the-underlying-SQL,
  hedged causal language, a "flag this answer" mechanism, a bounded confidence score). Cheaper to
  build these in alongside the first `/chat` route than to retrofit later — see
  `../../specs/10-trust-safety-compliance.md` §2.
- No privacy policy, ToS, or data retention/deletion policy exist yet. Not blocking for local dev,
  but should land before any non-test user's data is stored — same line the SQL user-scoping gap
  above already draws. See `../../specs/10-trust-safety-compliance.md` §4 (India DPDP Act).
