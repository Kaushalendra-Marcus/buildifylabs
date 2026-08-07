# CLAUDE.md — Backend

Guidance for Claude (or any agent) working in `Backend/`. Read this before making changes.

## What this is

FastAPI backend for an **AI Business Intelligence Copilot**: an Indian-SMB-focused product where a
user uploads business data (CSV/PDF/XLSX), asks questions in plain English, and gets back the right
chart, a plain-English explanation of *why* something happened, and actionable recommendations —
optionally correlated with real-world news.

Full product context, API contracts, edge cases, and acceptance criteria live in **`../specs/`**
(one file per module, `00-overview.md` first). That directory is the source of truth for intended
behavior — this file is the source of truth for how to work in this codebase day to day. Skim the
relevant spec file before touching a module; several files below have comments explaining *why*
something non-obvious was done, and those reasons matter — don't refactor them away without
re-reading the comment.

## Tech stack

- **FastAPI** (async), **Uvicorn**
- **PostgreSQL** via Neon.tech — **no Supabase** (blocked in India, this is a hard constraint, not
  a preference)
- **SQLAlchemy 2.x async** (`asyncpg` driver) + **Alembic** migrations
- **Auth**: `python-jose` (JWT, stateless — no server-side session store), `passlib[bcrypt]`,
  `google-auth` (Google ID token verification)
- **Email**: `aiosmtplib` (async SMTP)
- **LLM**: Groq (`llama-3.1-70b-versatile`, primary) → HuggingFace Inference API
  (`Mixtral-8x7B-Instruct`, fallback)
- **SQL safety**: `sqlglot` — AST-based validation, not regex/string blacklisting
- **Planned, not wired yet**: Pinecone (vectors), Redis (caching), Neo4j (graph store)

## Directory layout

```
app/
  main.py              FastAPI app, CORS, router registration, lifespan
  config.py            Settings (pydantic-settings, reads .env)
  db/
    database.py        Async engine/session factory, Base, get_db dependency
    models/            SQLAlchemy models — see models/__init__.py docstring before adding one
  routes/
    auth.py             Only router that exists so far
  middlewares/          FastAPI Depends()-based middleware (auth, plan, rate-limit, file, SQL)
  schemas/               Pydantic request/response models
  services/
    auth/                Registration, login, Google OAuth, guest auth, tokens, email
    llm/                 Groq client, structured insight pipeline, NL→SQL generator
  utils/
    usage.py             Single source of truth for daily-quota reset — see below
alembic/                 Migrations (one so far: initial_clean)
requirements.txt
docker-compose.yml       Currently empty — not yet configured
```

## Running locally

```bash
pip install -r requirements.txt
alembic upgrade head              # apply migrations
uvicorn app.main:app --reload     # dev server, http://localhost:8000
```

No test suite exists yet (no `tests/` dir, no test runner in `requirements.txt`). If you add tests,
introduce `pytest` + `pytest-asyncio` and a `tests/` directory; nothing currently constrains your
choice here.

No linter/formatter config exists in this directory (no `ruff`/`black` config). Match the existing
style (see comments in files like `sql_sanitizer.py`, `usage.py`, `models/__init__.py` for the
house style: explain the *why*, especially for non-obvious ordering/race-condition fixes).

### Required environment variables (`.env`, see `app/config.py`)

Required (app won't boot without these): `DATABASE_URL`, `JWT_SECRET`, `FRONTEND_URL`, `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM`, `GOOGLE_CLIENT_ID`.

Optional today (back features not yet wired into any route — see Module Status below):
`GROQ_API_KEY`, `HF_API_KEY`, `PINECONE_API_KEY`, `PINECONE_ENVIRONMENT`, `REDIS_URL`,
`GOOGLE_CLIENT_SECRET`, `UPI_ID`. Per `config.py`'s own comment: these become *required* again as
each feature actually ships — don't make them required speculatively.

`ALLOWED_ORIGIN` defaults to `["http://localhost:5173"]` (the Vite dev server default port) — keep
frontend and backend CORS config in sync if either changes.

## Module status (as of this audit — verify against `../specs/00-overview.md` for the latest)

| Module | Status |
|---|---|
| Auth (email/Google/guest, verify, reset) | ✅ Implemented — see `specs/01` for known gaps |
| Plan tiers + daily quota | ✅ Implemented |
| Payment (Razorpay) | ❌ Not implemented — model/schema still reflect an **old UTR/manual-UPI design that has been superseded**; see note below |
| File upload validation | ⚠️ Validator only — no storage backend, no route, no parsing |
| NL→SQL generation + safety sandbox | ⚠️ Sanitizer done and hardened; `clean_sql_response()` is an empty stub; no execution step; **no user-scoping on generated queries (blocking security gap, see specs/05)** |
| AI insight/visual pipeline | ⚠️ Core logic done (`langchain_pipeline.py` doesn't actually use LangChain despite the filename); unreachable — no route calls it |
| News context | ❌ Not started |
| Graph knowledge store (Neo4j) | ❌ Not started |

**⚠️ Payment module inconsistency to be aware of:** `specs/03-payment-verification.md` documents a
*revision* from manual UPI/UTR to Razorpay (order + webhook verification), but `app/db/models/payment.py`,
`app/schemas/payment.py`, and `UPI_ID`/`PAYMENT_AMOUNT` in `config.py` still reflect the **old**
UTR design. If you're picking up payments, the model/schema need to be migrated to match
`specs/03` (add `razorpay_order_id`, `razorpay_payment_id`, `razorpay_signature`; drop `utr`) before
building the route layer — don't build Razorpay routes against the current UTR-shaped model.

## Conventions that matter (don't silently "fix" these)

- **`app/db/models/__init__.py` must import every model.** SQLAlchemy resolves the string-based
  `relationship("...")` references lazily; if a model isn't imported somewhere before the first
  query, you get `InvalidRequestError: failed to locate a name`. New model → add the import here.
- **`app/utils/usage.py::reset_daily_usage_if_needed` is the only place daily-quota rollover logic
  should live.** It replaced two divergent implementations (rolling-24h vs. calendar-day) that used
  to disagree. Don't reintroduce quota-reset logic anywhere else.
- **`rate_limiter`'s check-and-increment is a single conditional `UPDATE ... RETURNING`,
  not separate `SELECT` + `UPDATE`.** This is what makes it race-safe under concurrent requests.
  Refactoring it back into two statements reintroduces a real bug, not just a style regression.
- **JWT token types share one secret, distinguished by a `type` claim** (`"access"`, `"refresh"`,
  `"verify"`, `"reset_password"`) — not separate secrets per purpose. `token_service.py` and
  `email_verification.py` both rely on this; keep new token kinds consistent with the pattern.
- **Auth error messages are deliberately generic** ("Invalid email or password", the same
  forgot-password response regardless of whether the email exists) to prevent user enumeration.
  Don't make these more specific for debugging convenience.
- **`sanitize_sql()` is the only place SQL safety logic should live.** It parses to a real AST via
  `sqlglot` rather than string-matching, specifically to catch write/DDL statements smuggled inside
  CTEs/subqueries. If you touch NL→SQL generation, safety checks stay centralized here — don't
  duplicate or second-guess them in the generator.
- **`DATABASE_SCHEMA` in `sql_generator.py` is a placeholder** (`sales`/`customers`/`orders` — none
  of which exist in this app's actual models). It needs to become dynamic per user/uploaded file
  once file ingestion (specs/04) lands — don't build against it as if it's real.

## Known gaps worth knowing before you extend nearby code

These are tracked in detail in the relevant spec file; summarized here so you don't rediscover them
the hard way:

1. `UserCreate.password` is `Optional` but `register_user()` assumes it's set — omitting it throws
   inside `passlib` instead of a clean 400. (`specs/01`)
2. Guest lookup with no `device_id` can raise `MultipleResultsFound` (`device_fingerprint IS NULL`
   matches multiple rows). (`specs/01`)
3. No rate limiting on `/auth/signin` or `/auth/verify-email` despite `LOGIN_RATE_LIMIT` /
   `VERIFY_EMAIL_RATE_LIMIT` already existing in config. (`specs/01`)
4. **No user-scoping on generated SQL** — nothing currently forces a generated query to filter to
   the requesting user's own data. Flagged as a **blocking gap before any multi-tenant use**.
   (`specs/05`)
5. `VisualOutput.visual_type` (`str`) and `PipelineOutput.confidence` (`float`) are unconstrained —
   should be `Literal[...]` and `Field(ge=0.0, le=1.0)` respectively. (`specs/06`)
6. No storage backend chosen for uploaded files yet — validation passes with nowhere to persist the
   file. (`specs/04`)

## Suggested build order (from `specs/00-overview.md`, still current)

1. Fix auth gaps #1–#2 above (cheap, isolated).
2. Finish `sql_generator.clean_sql_response()` + write an `execute_sql()` step.
3. Close the user-scoping gap (#4) — required before wiring real data to anything else.
4. `POST /files/upload` + minimal CSV parsing (Pinecone/embeddings can come later).
5. First end-to-end `POST /chat` route: query → SQL → execute → `run_pipeline` → response.
6. Payment module (migrate model to Razorpay shape first, per the note above).
7. News context — explicitly deferred, nothing else depends on it.
