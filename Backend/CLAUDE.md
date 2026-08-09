# CLAUDE.md — Backend

This file loads into context every session — keep it short and don't add detail here. Deep-dive
material lives in `docs/` and `../specs/`; open only the one file the current task actually needs.
**Don't read the whole `docs/` or `specs/` directory up front** — use the table in §2 to go straight
to the right file.

## 1. Orientation

FastAPI backend for **BuildifyLabs**, an AI Business Intelligence Copilot for Indian SMBs: upload
business data → ask questions in plain English → get the right chart, a root-cause explanation, and
recommendations. Async FastAPI + SQLAlchemy, Neon Postgres (not Supabase — blocked in India),
stateless JWT auth, Groq LLM primary / HuggingFace fallback. **Auth, plan/quota, and file upload
(CSV → per-user data table) are wired end-to-end today** — SQL generation, the insight pipeline,
payments, and news are all mid-build. Don't assume a module's status from memory; check
`../specs/00-overview.md`'s module map if it matters for the task (one read, not a habit).

## 2. What to read, by task

| Task | Read this — nothing else |
|---|---|
| Any task, first | This file only |
| Building/fixing auth | `../specs/01-authentication.md` |
| Building/fixing plan tiers or quota | `../specs/02-plan-quota-enforcement.md` |
| Building payments | `../specs/03-payment-verification.md` |
| Building file upload/ingestion | `../specs/04-file-upload-ingestion.md` |
| Building NL→SQL or the safety sandbox | `../specs/05-query-sql-safety.md` |
| Building the AI insight/visual pipeline | `../specs/06-ai-insight-pipeline.md` |
| Building news context | `../specs/07-news-context-module.md` |
| Building the graph knowledge store | `../specs/08-graph-knowledge-store.md` |
| Full architecture diagram, module status, build order | `../specs/00-overview.md` — read once, it's the index for everything above |
| Deciding what to prioritize / build next | `../specs/09-differentiation-and-gtm.md` — also has the reasoning for deferring news (07) and the graph store (08) |
| AI-output trust requirements, or the SQL user-scoping security gap | `../specs/10-trust-safety-compliance.md` |
| Changing a model, route, middleware, or service | The source file itself — short and authoritative, don't guess |
| "Why is this written this way, can I simplify it" | `docs/conventions.md` |
| Bug-hunting / "what's currently known-broken" | `docs/known-gaps.md` |
| Setup / env vars | §4 below, then `app/config.py` if you need the full var list |

## 3. Tech stack

FastAPI (async) + Uvicorn · PostgreSQL (Neon) + SQLAlchemy async + Alembic · `python-jose` JWT +
`passlib[bcrypt]` + `google-auth` · `aiosmtplib` · Groq (primary LLM) → HuggingFace (fallback) ·
`sqlglot` (AST-based SQL safety). Not wired yet: Pinecone, Redis, Neo4j, Razorpay SDK.

## 4. Setup

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload     # http://localhost:8000
```

Required env vars: `DATABASE_URL`, `JWT_SECRET`, `FRONTEND_URL`, `SMTP_HOST/PORT/USER/PASS`,
`EMAIL_FROM`, `GOOGLE_CLIENT_ID`. Everything else is optional until its module ships — full list
and reasoning is in `app/config.py` itself (inline comments), don't duplicate it here. Tests run
from `Backend/` via `python -m pytest` (112 tests; conftest supplies dummy env vars so no `.env`
is needed; the `pandas` dependency is expected for the ingestion parser). `docker-compose.yml` is
present but empty — don't assume a container workflow.

## 5. Hard invariants

Don't change these without reading `docs/conventions.md` first — each looks simplifiable but isn't:

- `app/db/models/__init__.py` must import every model.
- The window-rollover rule lives only in `app/utils/usage.py` (`window_elapsed_clause` + `QUOTA_WINDOW`).
- `rate_limiter`'s quota check is one conditional `UPDATE ... RETURNING` over both counters + the
  roll condition — never `SELECT` + `UPDATE`.
- All JWT types share one secret, distinguished by a `type` claim.
- Auth error messages are deliberately generic (anti-enumeration).
- SQL safety logic lives only in `sql_sanitizer.py`.
- SQL user-scoping (tenant isolation) lives only in `app/services/data/executor.py` — per-user data
  tables (`user_data_table_name`) + `assert_user_scoped`; never add a second scoping check elsewhere.

## 6. Working with `specs/`

`../specs/` (shared with the frontend) is the authoritative product spec, one file per module:
Problem Statement → Functional Requirements → API Contracts → Constraints → Edge Cases → Acceptance
Criteria. Status legend: ✅ Implemented · ⚠️ Partial · ❌ Not started. Open only the file for your
current module (§2) — after finishing work, update that spec's status/checkboxes in the same
change so the audit doesn't go stale.
