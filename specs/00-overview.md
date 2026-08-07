# AI Business Intelligence Copilot — Product Spec (Overview)

> Spec-driven development docs for this repo. Each file below covers one module in the format:
> Problem Statement → Functional Requirements → API Contracts → Constraints → Edge Cases & Error Handling → Acceptance Criteria.
> Generated from the project's design conversation + a direct read of the current codebase at
> `/home/kaushal/MY PROJECTS/buildifylabs/Backend`, so status tags below reflect what is actually
> implemented today, not just what was discussed.

**Status legend:** ✅ Implemented &nbsp;·&nbsp; ⚠️ Partially implemented &nbsp;·&nbsp; ❌ Not started

---

## 1. Problem Statement

Small and mid-size businesses generate data (sales, customers, orders) but lack the tooling or
expertise to turn it into decisions. Traditional BI tools (Power BI, Tableau) require manual
dashboard building and are complex for non-technical owners. This product lets a user upload their
business data (CSV/PDF/XLSX) and ask questions in plain language, and get back:

- The right visual for the question (not just "a chart")
- An explanation of **why** something happened, not just what happened
- Actionable recommendations
- Optional correlation with real-world news for their company/industry

Target differentiation vs. general tools (ChatGPT+Code Interpreter, Julius.ai, Power BI Copilot):
Indian SMB focus — UPI-based payment (no gateway), India-compatible infra (no Supabase), and a
root-cause/news-correlation layer on top of the usual chart+insight loop.

## 2. System Architecture (current target)

```
Frontend (React + Vite)
        │
        ▼
Backend (FastAPI, async)
        │
   ┌────┴─────────────────────────────────────┐
   │                                            │
Auth / Plan / Rate-limit           AI Pipeline (Groq → HF fallback)
   │                                            │
Neon.tech PostgreSQL              SQL Generator → SQL Sanitizer → DB
   │                                            │
                                    Pinecone (per-user namespaces)
                                    Redis (news cache, TTL)
```

## 3. Module Status Map

| # | Module | Status | Spec File |
|---|--------|--------|-----------|
| 1 | Authentication (email/Google/guest, verify, reset) | ✅ Implemented | `01-authentication.md` |
| 2 | Plan tiers + daily quota enforcement | ✅ Implemented | `02-plan-quota-enforcement.md` |
| 3 | Manual UPI + UTR payment verification | ⚠️ Model/schema only, no route | `03-payment-verification.md` |
| 4 | File upload validation + ingestion pipeline | ⚠️ Validator only, no route/pipeline | `04-file-upload-ingestion.md` |
| 5 | NL→SQL generation + SQL safety sandbox | ⚠️ Sanitizer done, generator incomplete | `05-query-sql-safety.md` |
| 6 | AI insight/visual pipeline (Groq + structured output) | ⚠️ Core done, unreachable via any route | `06-ai-insight-pipeline.md` |
| 7 | News context (RSS + Pinecone news namespace) | ❌ Not started | `07-news-context-module.md` |
| 8 | Frontend (chat UI, charts, dashboard) | ❌ Not started (Vite/React scaffold only) | — |

## 4. Tech Stack (current, from `requirements.txt` / `config.py`)

- **API**: FastAPI (async), Uvicorn
- **DB**: PostgreSQL via Neon.tech, SQLAlchemy (async, `asyncpg`), Alembic migrations
- **Auth**: `python-jose` JWTs (stateless), `passlib[bcrypt]`, `google-auth` for Google ID token verification
- **Email**: `aiosmtplib` (async SMTP)
- **LLM**: Groq API (primary, `llama-3.1-70b-versatile`), HuggingFace Inference API (fallback, `Mixtral-8x7B-Instruct`)
- **SQL safety**: `sqlglot` (AST-based parsing/validation, not regex)
- **Planned, not yet wired**: Pinecone (vector store), Redis (`REDIS_URL` optional in config, unused so far)

## 5. System-Wide Constraints

- **No Supabase** — blocked in India. Neon.tech Postgres is the DB of record.
- **No payment gateway** (Razorpay etc.) — manual UPI transfer + UTR submission, admin-verified.
- **Free-tier infra budget**: Render (cold start ~30–40s on free tier), Groq free-tier rate limits,
  HuggingFace fallback is best-effort only (slower, less reliable, not JSON-schema-reliable).
- **Stateless JWT auth** — no server-side session store; access token TTL is 60 min, refresh TTL is
  7 days (`config.py` values — authoritative over any earlier discussion of 15 min).
- **SQL execution must never allow write/DDL** — enforced via AST parsing (`sql_sanitizer.py`), not
  string blacklisting.
- **Single source of truth for daily-quota reset** — `app/utils/usage.py::reset_daily_usage_if_needed`.
  This existed as two divergent implementations before being consolidated; must not be re-duplicated.

## 6. Known Cross-Cutting Gaps (found during this audit)

These are called out in detail in their respective module specs, listed here for visibility:

1. **`UserCreate.password` is `Optional` but `register_user()` assumes it's always present** — an
   omitted password will throw an unhandled error inside `passlib` rather than a clean validation
   error. (`01-authentication.md`)
2. **Guest lookup can raise `MultipleResultsFound`** if `device_id` is omitted, since
   `device_fingerprint IS NULL` can match more than one row and `scalar_one_or_none()` requires ≤1
   match. (`01-authentication.md`)
3. **No admin role exists on `User`** — the payment verification module needs an admin-only surface
   and there is currently no mechanism to identify an admin. (`03-payment-verification.md`)
4. **No storage backend chosen** for uploaded files — validation passes but there's nowhere to
   persist the file afterward. (`04-file-upload-ingestion.md`)
5. **No automatic user-scoping on generated SQL** — nothing currently guarantees a generated query
   only reads the requesting user's own uploaded data. This is a **blocking security gap** before
   multi-tenant use. (`05-query-sql-safety.md`)
6. **`VisualOutput.visual_type` and `PipelineOutput.confidence` are unconstrained types** (`str` /
   `float` instead of `Literal[...]` / bounded `Field`) — an out-of-range or invalid value from the
   LLM currently passes schema validation. (`06-ai-insight-pipeline.md`)
7. **No route exists yet that connects the pieces** — SQL generation, SQL execution, and the AI
   insight pipeline are all individually functional but nothing calls them end-to-end from an HTTP
   endpoint. This is the critical path to a working demo.

## 7. Suggested Build Order (from current state)

1. Close gaps #1–#2 (auth bugs) — cheap, isolated fixes.
2. Finish `sql_generator.clean_sql_response` + write a `execute_sql()` step (`05`).
3. Close gap #5 (user-scoping) — required before wiring anything else to real data.
4. Build `POST /files/upload` + minimal CSV parsing (skip Pinecone/embeddings for a first pass —
   query the parsed table directly) (`04`).
5. Build the first end-to-end `POST /chat` route: query → SQL → execute → `run_pipeline` → response
   (`05` + `06`).
6. Payment module (`03`) — needed for monetization but not for the core demo loop.
7. News context (`07`) — explicitly deferred; adds real value but nothing else depends on it.
