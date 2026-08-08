# AI Business Intelligence Copilot — Product Spec (Overview)

> Spec-driven development docs for this repo. Each file below covers one module in the format:
> Problem Statement → Functional Requirements → API Contracts → Constraints → Edge Cases & Error Handling → Acceptance Criteria.
> Status tags reflect what is actually implemented today, not just what was discussed — kept current
> as of this revision, which corrects an earlier draft's assumption of an India-only SMB market (see
> §1) and adds prediction/calculation as a core capability (see §3, `11-prediction-and-calculation.md`).

**Status legend:** ✅ Implemented &nbsp;·&nbsp; ⚠️ Partially implemented &nbsp;·&nbsp; ⏸️ Paused &nbsp;·&nbsp; ❌ Not started

---

## 1. Problem Statement

Businesses generate data (sales, customers, orders) but lack the tooling or expertise to turn it
into decisions. Traditional BI tools (Power BI, Tableau) require manual dashboard building and are
complex for non-technical owners. This product lets a user upload their business data (CSV/PDF/XLSX)
and ask questions in plain language, and get back:

- The right visual for the question (not just "a chart")
- An explanation of **why** something happened, not just what happened
- **Forecasts, what-if scenarios, statistical calculations, and competitor benchmarking** — not
  just descriptive analysis of what already happened (`11-prediction-and-calculation.md`)
- Actionable recommendations
- Optional correlation with real-world news for their company/industry

**Market: global and industry-agnostic by design** — any business, any size, anywhere, not a
specific vertical or country. (An earlier draft of this spec assumed an India-only SMB market; that
was a wrong assumption corrected during design review, not a real product decision — see
`09-differentiation-and-gtm.md` §2.) India-specific choices that remain (Neon Postgres over
Supabase, which is blocked in India) are practical infra decisions made for the founder's own
market, not a signal the product is India-only.

Target differentiation vs. general tools (ChatGPT+Code Interpreter, Julius.ai, Power BI Copilot): a
root-cause/prediction/news-correlation layer on top of the usual chart+insight loop, reliability
through a multi-LLM cascade rather than a single provider (`12-llm-orchestration.md`), and an
"ask, don't guess" trust posture (`10-trust-safety-compliance.md`). None of this is a fully durable
moat on its own — see `09-differentiation-and-gtm.md` for the fuller strategy (global distribution,
integration-based friction reduction, GTM channel).

## 2. System Architecture (current target)

```
Frontend (Next.js)
        │
        ▼
Backend (FastAPI, async)
        │
   ┌────┴─────────────────────────────────────┐
   │                                            │
Auth / Usage quota / Rate-limit    AI Pipeline (multi-LLM cascade —
   │                                 Groq → Gemini → open-source → Claude,
Neon.tech PostgreSQL                see 12-llm-orchestration.md)
   │                                            │
                                    SQL Generator → SQL Sanitizer → DB
                                    Prediction/calculation engine (pandas —
                                    computed in code, narrated by LLM, see 11)
                                            │
                                    Pinecone (per-user namespaces)
                                    Redis (news cache, TTL)
```

The frontend is a real, substantially-built Next.js app (chat interface, resizable dashboard
workspace, 7 working visual components) — not the empty scaffold earlier drafts of this spec
described. It previously used a third-party SaaS (Tambo AI) for AI-to-UI routing; that's being
removed. See `13-frontend-migration.md` for full detail and current migration status.

## 3. Module Status Map

| # | Module | Status | Spec File |
|---|--------|--------|-----------|
| 1 | Authentication (email/Google/guest, verify, reset) | ✅ Implemented | `01-authentication.md` |
| 2 | Usage quota enforcement (rolling window + lifetime cap) | 🔶 Redesigned, needs rewrite from old daily-tier model | `02-plan-quota-enforcement.md` |
| 3 | Razorpay payment | ⏸️ Paused — staying free for now, see `02` | `03-payment-verification.md` |
| 4 | File upload validation + ingestion pipeline | ⚠️ Validator only, no route/pipeline | `04-file-upload-ingestion.md` |
| 5 | NL→SQL generation + SQL safety sandbox | ⚠️ Sanitizer done, generator incomplete | `05-query-sql-safety.md` |
| 6 | AI insight/visual pipeline (structured output) | ⚠️ Core done, unreachable via any route; contract updated to match real frontend components | `06-ai-insight-pipeline.md` |
| 7 | News context (RSS + Pinecone news namespace) | ❌ Not started — deferred, see `09` §8 | `07-news-context-module.md` |
| 8 | Graph knowledge store (Neo4j entity/relationship retrieval) | ❌ Not started — deferred, see `09` §8 | `08-graph-knowledge-store.md` |
| 11 | Prediction, calculation & benchmarking | ❌ Not started, core capability not a later add-on | `11-prediction-and-calculation.md` |
| 12 | Multi-LLM orchestration | ❌ Not started, extends existing Groq/HF pattern | `12-llm-orchestration.md` |
| 13 | Frontend consolidation (Tambo removal, Next.js migration) | 🔶 Decision made, physical migration pending | `13-frontend-migration.md` |

**Strategy & requirements docs** (not implementation modules, so not status-tracked the same way):
`09-differentiation-and-gtm.md` (positioning, distribution, retention, monetization posture) and
`10-trust-safety-compliance.md` (AI-output trust requirements, the blocking security gap in row 5,
and global data-protection compliance).

## 4. Tech Stack (current target)

- **API**: FastAPI (async), Uvicorn
- **DB**: PostgreSQL via Neon.tech, SQLAlchemy (async, `asyncpg`), Alembic migrations
- **Auth**: `python-jose` JWTs (stateless), `passlib[bcrypt]`, `google-auth` for Google ID token verification
- **Email**: `aiosmtplib` (async SMTP) — also backs the contact-form flow in `02` §2 FR5
- **LLM**: multi-provider cascade — Groq, Gemini, an open-source option, and Claude reserved for
  SQL-reliability and final synthesis (`12-llm-orchestration.md`). Supersedes the earlier
  Groq-primary/HuggingFace-fallback-only design, which remains the interim implementation until `12`
  is built.
- **SQL safety**: `sqlglot` (AST-based parsing/validation, not regex)
- **Frontend**: Next.js (App Router), Zustand for client state, Tailwind — real components already
  built, no third-party generative-UI SaaS (`13-frontend-migration.md`)
- **Planned, not yet wired**: Pinecone (vector store), Redis (`REDIS_URL` optional in config, unused so far)

## 5. System-Wide Constraints

- **No Supabase** — blocked in India, and Neon.tech Postgres works fine globally too, so this is a
  constraint with no real downside for the broader market.
- **No payment gateway for now** — the product is free while it validates usage; see `02` for the
  rolling-quota + contact-form model, and `03` for the paused Razorpay design.
- **Free-tier infra budget**: Render (cold start ~30–40s on free tier), and multiple LLM providers'
  free tiers now instead of just Groq's — see `12-llm-orchestration.md` for how the cascade manages
  this.
- **Stateless JWT auth** — no server-side session store; access token TTL is 60 min, refresh TTL is
  7 days (`config.py` values — authoritative over any earlier discussion of 15 min).
- **SQL execution must never allow write/DDL** — enforced via AST parsing (`sql_sanitizer.py`), not
  string blacklisting.
- **Single source of truth for usage-quota rollover** — was `app/utils/usage.py::reset_daily_usage_if_needed`
  (calendar-day model), now needs to become an equivalent single-source rolling-window function per
  `02`. The *principle* (exactly one place this logic lives) carries over unchanged; the *mechanic*
  does not.
- **Arithmetic is computed in code, never by the LLM** — applies to `11-prediction-and-calculation.md`'s
  forecasts/what-ifs/stats: the LLM narrates a number that was already calculated deterministically,
  never produces the number itself.

## 6. Known Cross-Cutting Gaps (found during this audit)

These are called out in detail in their respective module specs, listed here for visibility:

1. **`UserCreate.password` is `Optional` but `register_user()` assumes it's always present** — an
   omitted password will throw an unhandled error inside `passlib` rather than a clean validation
   error. (`01-authentication.md`)
2. **Guest lookup can raise `MultipleResultsFound`** if `device_id` is omitted, since
   `device_fingerprint IS NULL` can match more than one row and `scalar_one_or_none()` requires ≤1
   match. (`01-authentication.md`)
3. **No admin role exists on `User`** — not currently blocking (payment is paused), but will matter
   again once Razorpay support/refunds are eventually built. (`03-payment-verification.md`)
4. **No storage backend chosen** for uploaded files — validation passes but there's nowhere to
   persist the file afterward. (`04-file-upload-ingestion.md`)
5. **No automatic user-scoping on generated SQL** — nothing currently guarantees a generated query
   only reads the requesting user's own uploaded data. This is a **blocking security gap** before
   multi-tenant use. (`05-query-sql-safety.md`; full trust/compliance framing in
   `10-trust-safety-compliance.md` §3)
6. **No route exists yet that connects the pieces** — SQL generation, SQL execution, and the AI
   insight pipeline are all individually functional but nothing calls them end-to-end from an HTTP
   endpoint. This is the critical path to a working demo.
7. **A Tambo AI API key was found committed/exposed client-side** in the frontend's old `.env` —
   needs rotation and git-history scrubbing, neither of which a filesystem-only tool can do.
   (`13-frontend-migration.md` §4)
8. **Guest lifetime-cap enforcement is best-effort, not airtight** — tracked via device fingerprint,
   which a guest can reset by clearing cookies/switching devices. Accepted tradeoff, not a blocker.
   (`02-plan-quota-enforcement.md` §5)

## 7. Suggested Build Order (from current state)

1. Close gaps #1–#2 (auth bugs) — cheap, isolated fixes.
2. Rewrite the quota system per `02` (rolling window + lifetime cap, replacing the old daily-tier
   model) — this is now ahead of SQL/pipeline work because it's small, self-contained, and every
   other module depends on quota enforcement existing correctly underneath it.
3. Finish `sql_generator.clean_sql_response` + write an `execute_sql()` step (`05`).
4. Close gap #5 (user-scoping) — required before wiring anything else to real data.
5. Build `POST /files/upload` + minimal CSV parsing (skip Pinecone/embeddings for a first pass —
   query the parsed table directly) (`04`).
6. Build the first end-to-end `POST /chat` route: query → SQL → execute → `run_pipeline` → response
   (`05` + `06`), including statistical calculations (`11` §3.1, the lowest-risk piece of that
   module) in the same pass since they depend on the same data-fetch path. Build in the trust
   requirements from `10-trust-safety-compliance.md` §2 (traceable SQL, hedged causal language,
   `QueryLogs` writes, the clarifying-question pattern) at the same time — much cheaper to include
   now than retrofit onto a shipped chat UI later.

   **→ Checkpoint: put this in front of real users before continuing.** Steps 7+ below are ordered
   by priority, not committed scope — see `09-differentiation-and-gtm.md` §8. Define what "worth
   continuing" looks like before this point, not after (e.g. % of first-time users asking a second
   question in the same session) — without a stated bar it's easy to talk yourself past a weak
   signal.

7. Multi-LLM orchestration (`12`) — the single-provider setup is a real reliability risk the moment
   real users depend on this working.
8. Forecasting, what-ifs, and benchmarking (`11` §3.2–3.4) — the rest of the prediction module,
   once the core loop and statistical-calculation foundation from step 6 are proven.
9. Finish the frontend migration (`13`) if not already done — physical move, Tambo removal, the
   `06`/frontend visual-contract reconciliation, and the leaked-key cleanup.
10. Integrations + distribution (`09` §3–§4: Sheets/Shopify/QuickBooks, WhatsApp interface) —
    reduce friction on a validated loop, ahead of any new capability.
11. News context (`07`) and the graph knowledge store (`08`) — explicitly deferred until the core
    loop has real-user evidence behind it. Real engineering effort against value that's currently
    speculative; don't build concurrently with proving the core loop.
12. Payment (`03`) — paused, not scheduled. Revisit only once usage data suggests it's worth
    building, per the free-forever posture in `02` and `09` §7.
