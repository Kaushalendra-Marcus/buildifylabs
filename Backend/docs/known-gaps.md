# Known gaps — quick index

Compiled across all spec files for fast scanning. Each entry is one line — if you're actually
fixing one, open the cited spec file for the full edge-case writeup and acceptance criteria before
starting; don't fix from this summary alone.

## Tooling (not a product gap — local dev contract)

- Tests (and uvicorn) must run with the project interpreter `Backend/.venv/bin/python`
  (e.g. `.venv/bin/python -m pytest -q` from `Backend/`); the system python lacks deps
  (`pandas`, `pypdf`, ...). `pypdf` is the PDF text extractor: lightweight, no native deps.

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
- **[Closed]** Resend-verification endpoint shipped: `POST /auth/resend-verification`
  (same generic response as forgot-password, IP rate-limited via
  `verify_email_rate_limit`, only unverified email/password accounts get a link).

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
- **[Closed, Part C]** `.xlsx` uploads parse into the per-user SQL table exactly like CSV
  (`parser.parse_xlsx_bytes` → same `clean_dataframe` + `upsert_user_table`; `openpyxl`).
- **[Closed, Part C]** `.pdf` uploads extract text (`pdf_parser.extract_pdf_text`/`chunk_text`)
  and store plain-text chunks in `document_chunks` on the existing Neon Postgres
  (no embedding service, no second vendor/key — the earlier HF-Inference-API +
  pgvector design was removed after the vendor endpoint became unreachable from
  prod). Retrieved per query by keyword overlap scoped to `user_id` (zero
  keyword hits returns nothing so the judge can clarify instead of answering
  off-topic; only summarize-style queries fall back to the opening chunks),
  merged into the answer inside a
  reserved sub-budget (`MAX_DOCUMENT_CONTEXT_CHARS`) ranked ahead of web
  evidence, tagged `your_documents` end-to-end (prompt tag + frontend "your
  documents" badge). `ENABLE_DOCUMENT_QA=false` degrades cleanly (ingest still
  succeeds; retrieval skipped). Scanned/image-only PDFs fail honestly (OCR out
  of scope).
- **[Closed]** File management round-trip exists: `DELETE /files/{id}` (own-only,
  guest 403; drops the per-user data table only when the deleted file is the
  latest completed tabular upload, deletes PDF `document_chunks`, removes raw
  bytes best-effort) and `GET /files/{id}/preview` (first 10 table rows without
  the synthetic `id`, or first 10 PDF chunks — no embedding call). Single-PDF
  replace stays append-by-design; cross-document re-ranking is still open.
- **[Accepted]** Size check loads the whole file into memory before checking size — fine at today's
  ≤10MB cap, won't scale if caps are raised without moving to streaming checks.
- **[B3 contract note]** A fresh CSV/XLSX upload **replaces** the user's per-user data table (one active data
  file per user, per `specs/04` §4). PDFs accumulate in `document_chunks` instead (append, don't
  replace). `FileUpload.pinecone_namespace` holds the per-user table name (CSV/XLSX) or a
  `vector:<upload_id>` ref (PDF).

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

## Live-web evidence tools (`app/services/web_search.py`, `specs/11` §3.3–3.4 partial)

- Yahoo `quoteSummary` needs a crumb+cookie handshake (`_get_yahoo_crumb`, 30-min process cache,
  one fresh-crumb retry on 401) — an undocumented endpoint that can break or throttle without
  notice; any failure skips fundamentals gracefully, never the request.
- FRED macro series need `FRED_API_KEY` (free, 32-char) — absent means the macro adapter logs
  and skips; only 4 fixed series mapped (CPIAUCSL/UNRATE/FEDFUNDS/GDP).
- Wikipedia grounding is entity-driven (max 2, 600-char summaries); disambiguation pages are
  skipped, not guessed.
- Tavily Extract fires only on a verbatim URL in the query (1 URL, 4000 chars, needs the Tavily
  key) — follow-ups about a cited article without a pasted URL still use snippets.
- What-if v1 (`stats.py::apply_what_if`) is price-scenarios only with the quantity-unaffected
  assumption; no elasticity, no cost/discount-column modeling beyond effective-price inversion.
- Forecasting v1 (`stats.py::compute_forecast`, specs/11 §3.2) is linear-regression extrapolation
  over the executed rows (single numeric column only, ≥4 points, else None — no forecast, not a
  guess), narrated under the FORECAST RULE with an Actual/Projected graph visual. No
  seasonal/ARIMA modeling; web/market-history series are out of scope (own uploaded data only).
- Judge-directed routing (`plan_tools` → `tools_needed`) is advisory: one extra fast-model call
  per live-web request (overlapped with SQL generation, not serial), and any planner failure or
  empty/unknown plan degrades to the deterministic intent predicates. The regex predicates are
  therefore still the guarantee path, not dead code — do not delete them.

## Hallucination guards (post-narration, deterministic)

All in `app/services/llm/pipeline/` unless noted; every guard records itself via
`guard_metrics.record_guard` and the events land in `research_state["guards"]`
(persisted in QueryLogs; aggregate with `scripts/guard_report.py`).

- **Prose number guard** (`prose_guard.py`, `PROSE_NUMBER_GUARD_MODE=off|flag|strip`,
  default `flag`): every number in answer/summary/insights/root causes/
  recommendations must trace to rows, computed stats, structured history,
  snippets or the user's own question, or be a sum/difference/ratio/share/%-change
  of two of those. `flag` caps confidence at 0.65 and notes it in the trace;
  `strip` also removes the sentence/item. Whole numbers 0-10 are treated as
  counts unless written with `%`. Known limits: a fabricated number that happens
  to equal a derived value passes; paraphrased magnitudes ("about a third") are
  not checked.
- **Citation-support guard** (`CITATION_SUPPORT_GUARD_MODE`, same modes): a
  sentence citing `[n]` needs >=15% content-word overlap with snippet n (when it
  has 5+ content words) and its non-structural numbers must be in, or derivable
  from, that snippet. Lexical, not semantic: a false claim that reuses the
  snippet's vocabulary still passes.
- **Fail-closed** (`FAIL_CLOSED_GUARDS`): if the narration contract or a prose
  guard crashes, confidence is capped at 0.65 and the trace says verification was
  unavailable (previously the prose shipped silently as "fail-open").
- **Prose-rescue path** (`PROSE_RESCUE_STRICT`): sentences with untraceable
  numbers are removed; nothing left -> honest zero-confidence fallback.
- **Number placeholders** (`ENABLE_NUMBER_PLACEHOLDERS`, default off; changes the
  narration prompt, evaluate with the golden set before enabling): the narrator
  writes `{{computed.x.y}}` and the server fills in the value.
- **Untrusted evidence**: SYSTEM_PROMPT has an UNTRUSTED EVIDENCE RULE and a NUMBER
  RULE; `evidence_safety.py` neutralizes instruction-shaped text in the prompt copy
  of snippets (grounding still uses the original text). Pattern-based, so evadable;
  the output guards are the real backstop.
- **SQL**: `LIMIT` capped at 1000 (`sql_sanitizer.MAX_QUERY_LIMIT`); Postgres
  `statement_timeout` via `SQL_STATEMENT_TIMEOUT_MS`; a data profile (types, ranges,
  top values, latest date) is fed to SQL generation so relative dates resolve
  against the data (`ENABLE_DATA_PROFILE`); result sanity notes in
  `research_state["sql_sanity"]`; plain-English `research_state["sql_interpretation"]`.
  The profile costs one extra aggregate scan per own-data request (fine at the
  current <=10MB cap; cache by upload id if caps grow).
- **Drivers** (`app/services/data/drivers.py`, `ENABLE_DRIVER_ANALYSIS`): "why did X
  change" questions get a code-computed per-segment decomposition (latest window vs
  the previous equal window, anchored on the data's latest date) in
  `computed["drivers"]`; root causes must come from it.
- **Not done**: the upload cleaning report (what `clean_dataframe` coerced/dropped)
  is not stored or surfaced; needs a column/migration on `FileUpload`. The new
  `research_state` keys are not rendered in the frontend yet. SQL self-consistency
  (generate twice, compare) was skipped: it doubles LLM spend against a 4-per-6h quota.
- **Evals**: `scripts/eval_pipeline.py --repeat N --min-pass-rate R` (pass@k, flake
  detection) and `--record-llm/--replay-llm` (offline re-run of a recording);
  `scripts/eval_sql.py` (text-to-SQL execution accuracy; `--dry-run` verifies the
  fixtures offline and runs in pytest via `tests/test_eval_tooling.py`).

## LLM config (`specs/12-llm-orchestration.md`)

- `GROQ_MODEL` is a required env var (no code default — tests use `test-model`
  mocks). Groq shut down `llama-3.3-70b-versatile` / `llama-3.1-8b-instant` for
  free/dev tiers on Aug 16, 2026; their recommended replacements are
  `openai/gpt-oss-120b` (strong) and `openai/gpt-oss-20b` (fast). The local
  `Backend/.env` already runs `GROQ_MODEL=openai/gpt-oss-120b` — if chat answers
  degrade to fallbacks, check that var first (a dead id fails 3x with backoff
  before the HuggingFace fallback). Do NOT use `openai/gpt-oss-safeguard-20b`
  here (a safety classifier, not a chat model — it breaks narration).

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
