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

- **[Closed, B3] Storage backend decided** — local disk for dev (`app/services/data/storage.py`,
  `UPLOAD_DIR`), object store (S3) for prod; the module is the swap seam.
- **[Closed, B3]** Empty (0-byte) uploads are rejected explicitly with a `400` in the validator.
- **[Graceful, B3]** `.xlsx` / `.pdf` uploads pass validation (per FR3) but fail ingestion with a
  stored reason (`status="failed"`, `error` set) — parsing beyond CSV is deferred. The raw file is
  still persisted.
- **[Accepted]** Size check loads the whole file into memory before checking size — fine at today's
  ≤10MB cap, won't scale if caps are raised without moving to streaming checks.
- **[B3 contract note]** A fresh upload **replaces** the user's per-user data table (one active data
  file per user, per `specs/04` §4). `FileUpload.pinecone_namespace` temporarily holds the per-user
  table name as the storage ref until Pinecone ships.

## NL→SQL (`specs/05-query-sql-safety.md`)

- **[Closed, B2]** User-scoping is now structural (per-user data tables via
  `app/services/data/executor.py::user_data_table_name`) plus post-generation validation
  (`assert_user_scoped` rejects any table outside the caller's namespace).
- **[Closed, B2]** `clean_sql_response()` implemented (plain/fenced/prose extraction) and tested.
- **[Closed, B4]** Real, typed per-file columns land in the per-user table (B3) and are introspected
  via `get_table_columns()` + `build_data_schema()` into the `/chat` prompt — the
  `sales`/`customers`/`orders` placeholder is now only a documented fallback.
- **[Closed, B4]** `INVALID_QUERY`'s `InvalidQueryError` short-circuits to a graceful
  "couldn't turn that into a query" response in `POST /chat` (still logged to `QueryLogs`).

## AI insight pipeline (`specs/06-ai-insight-pipeline.md`)

- **[Closed, B4]** `VisualOutput.visual_type` is a `Literal` of the 7 real frontend types and
  `PipelineOutput.confidence` is `Field(ge=0.0, le=1.0)` — invalid LLM values now fail validation
  and the pipeline falls back to a bounded default.
- **[Closed, B4]** Large `db_data` is truncated (`_truncate_rows`, `PROMPT_MAX_ROWS=50`) before
  injection — context-window risk on the input side is handled.
- **[Closed, B4]** `run_pipeline` is reachable via `POST /chat` (`app/routes/chat.py`).
- **[Closed, B4]** `langchain_pipeline.py` matches spec `06` §3: 7 real `visual_type`s, `props`,
  bounded `confidence`, `clarification` alternate mode, and traceability fields (`sql_query`,
  `data_preview`, `query_log_id`).
- **[Closed, B4]** `run_pipeline(news_context: list = [])` mutable default fixed to `None`.

## LLM config (`specs/12-llm-orchestration.md`)

- `config.py`'s `GROQ_MODEL` default (`llama-3.1-70b-versatile`) is a Groq model ID decommissioned
  since ~Jan 2025. Without an env override, every `generate_response()` call fails 3x (with
  backoff) before silently falling to the HuggingFace fallback — meaning today's interim pipeline
  is quietly running on HF, not Groq, on every request. Needs a live model id from
  `console.groq.com/docs/models` — that lineup moves fast (e.g. the interim replacement
  `llama-3.3-70b-versatile` is itself being retired Aug 16, 2026).

## Cross-cutting

- No admin role exists on `User` at all — relevant once payments need manual override/refund support.
- **[Closed, B4]** `QueryLogs` (`app/db/models/query_logs.py`) is written on every `/chat`
  request+response pair (incl. graceful fallbacks) and supports flagging via `POST /chat/flag`.
- **[Closed, B4]** The core trust affordances exist on the live `/chat` route: exact underlying SQL
  (`sql_query`) + raw row slice (`data_preview`) for "show the query", hedged causal language in the
  prompt's `root_causes`/`recommendations`, a bounded `confidence`, and a "flag this answer"
  endpoint. See `../../specs/10-trust-safety-compliance.md` §2.
- No privacy policy, ToS, or data retention/deletion policy exist yet. Not blocking for local dev,
  but should land before any non-test user's data is stored — same line the SQL user-scoping gap
  above already draws. See `../../specs/10-trust-safety-compliance.md` §4 (India DPDP Act).
