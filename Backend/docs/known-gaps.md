# Known gaps — quick index

Compiled across all spec files for fast scanning. Each entry is one line — if you're actually
fixing one, open the cited spec file for the full edge-case writeup and acceptance criteria before
starting; don't fix from this summary alone.

## Auth (`specs/01-authentication.md`)

- `UserCreate.password` is `Optional` but `register_user()` assumes it's set — omitting it throws
  inside `passlib` instead of a clean 400.
- Guest lookup with no `device_id` can raise `MultipleResultsFound` (`device_fingerprint IS NULL`
  legally matches multiple rows).
- `signup`/`signin` wrap their whole body in `except Exception as e: raise HTTPException(400, str(e))`
  — a genuine 500 (DB down, unexpected bug) gets misreported as a 400 validation error, and the raw
  exception string leaks to the client.
- No rate limiting on `/auth/signin` or `/auth/verify-email` — both brute-forceable, despite
  `LOGIN_RATE_LIMIT`/`VERIFY_EMAIL_RATE_LIMIT` already existing in config.
- Password reset tokens are time-limited (20 min) but not single-use — a leaked link stays valid
  for the rest of its window even after a successful reset.
- No resend-verification-email endpoint — a user with a lost/expired link has no self-serve way back in.

## Plan/quota (`specs/02-plan-quota-enforcement.md`)

- **[Accepted limitation]** Guest accounts are tracked by `device_fingerprint` only — a guest who
  clears cookies / uses a new device resets their apparent **lifetime** cap. Documented in `02` §5;
  revisit only if real guest abuse appears, not preemptively. (Window quota is unaffected.)
- `plan_checker` logs a warning on unrecognized `plan` values, but nothing currently surfaces it —
  add observability on those logs if bad data starts showing up.

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

- **[Closed, B2]** User-scoping is now structural (per-user data tables via
  `app/services/data/executor.py::user_data_table_name`) plus post-generation validation
  (`assert_user_scoped` rejects any table outside the caller's namespace).
- **[Closed, B2]** `clean_sql_response()` implemented (plain/fenced/prose extraction) and tested.
- `build_data_schema()` / `build_sql_prompt(schema=...)` exist, but B3's file ingestion must feed
  them the **real per-file column metadata** — until then the prompt still falls back to the
  `sales`/`customers`/`orders` placeholder schema.
- B2's `INVALID_QUERY` sentinel short-circuits in `execute_sql` (`InvalidQueryError`); the
  user-facing "couldn't turn that into a query" message renders with B4's `POST /chat`.

## AI insight pipeline (`specs/06-ai-insight-pipeline.md`)

- `VisualOutput.visual_type` (`str`) and `PipelineOutput.confidence` (`float`) are unconstrained —
  should be `Literal[...]` and `Field(ge=0.0, le=1.0)`; an invalid LLM value currently passes
  validation and reaches the caller as-is.
- No truncation/summarization step before large `db_data` is injected into the prompt — risk of
  blowing the model's context window on the input side.
- No route calls `run_pipeline` yet — it's fully built but unreachable.
- `langchain_pipeline.py` hasn't actually been updated to match spec `06` §3's contract yet:
  `VisualOutput.visual_type` is still a plain `str` (not the `Literal[...]` of 7 real types),
  `PipelineOutput.confidence` is still unbounded, there's no `clarification` field, and
  `SYSTEM_PROMPT` still tells the model to use the old 9 fictional visual types. The contract is
  fully designed in `06` §3 — just not applied to the source file.
- `run_pipeline(news_context: list = [])` uses a mutable default argument — not currently exploited
  (never mutated in place), but a footgun for the next person who touches this function.

## LLM config (`specs/12-llm-orchestration.md`)

- `config.py`'s `GROQ_MODEL` default (`llama-3.1-70b-versatile`) is a Groq model ID decommissioned
  since ~Jan 2025. Without an env override, every `generate_response()` call fails 3x (with
  backoff) before silently falling to the HuggingFace fallback — meaning today's interim pipeline
  is quietly running on HF, not Groq, on every request. Needs a live model id from
  `console.groq.com/docs/models` — that lineup moves fast (e.g. the interim replacement
  `llama-3.3-70b-versatile` is itself being retired Aug 16, 2026).

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
