<div align="center">
  <img src="Frontend/public/logo.png" width="112" alt="BuildifyLabs fox logo" />

  # BuildifyLabs

  **Ask your business data anything. Get answers you can trace.**

  Upload a CSV, PDF, or spreadsheet, ask a question in plain English, and get
  the right chart, the explanation behind it, and the exact query it came from.

  [![Frontend](https://img.shields.io/badge/frontend-React%2019%20%2B%20Vite-6d4df2?style=flat-square)](#tech-stack)
  [![Backend](https://img.shields.io/badge/backend-FastAPI%20%2B%20PostgreSQL-0d9488?style=flat-square)](#tech-stack)
  [![Tests](https://img.shields.io/badge/frontend%20tests-200%20passing-2f8a63?style=flat-square)](#testing--quality)
  [![CI](https://img.shields.io/badge/CI-lint%20%C2%B7%20test%20%C2%B7%20build-17141f?style=flat-square)](.github/workflows/ci.yml)
</div>

---

BuildifyLabs is an AI business-intelligence copilot for small and medium
businesses. It replaces the dashboard-building loop with a conversation:
your files become queryable data, questions become real SQL, and answers
arrive as purpose-built visual cards with evidence attached.

It is built around one rule: **an answer is only useful if you can check it.**
Every normal answer includes the query behind it, a confidence signal, the
sources or rows used, and a way to flag anything that looks wrong.

![BuildifyLabs landing page](screenshots/ui-landing.png)

## Table of contents

- [Why BuildifyLabs](#why-buildifylabs)
- [Product tour](#product-tour)
- [Feature highlights](#feature-highlights)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Environment reference](#environment-reference)
- [API reference](#api-reference)
- [Testing and quality](#testing--quality)
- [Deployment](#deployment)
- [Design system](#design-system)
- [Security and privacy](#security-and-privacy)
- [Spec-driven development](#spec-driven-development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

## Why BuildifyLabs

Most BI tools stop at "here is a chart." BuildifyLabs is designed for the next
questions:

- **What changed?** The answer text names the movement, while the visual carries
  the real values — never a decorative placeholder.
- **Why did it change?** Root causes are surfaced as hedged, evidence-backed
  factors instead of confident guesses.
- **What should I do next?** Recommendations and follow-up questions turn an
  answer into a next action.
- **Where did that come from?** The SQL, row preview, source receipts, and
  confidence score are part of the answer, not a separate debugging screen.
- **What if I need outside context?** A per-question scope switch lets you ask
  your data, the live web, or both — and the UI always shows which scope was
  actually used.

## Product tour

### Landing page

A soft lilac/ink design language with oversized centered type, a live question
box, floating product cards, and a browser mock of the actual chat workspace.
The landing page is a real product surface: typing a question carries it through
signup to the composer, and starter prompts fill the ask box.

### Authentication

Email/password, Google Identity Services, and guest entry are supported from the
same split editorial layout. Email verification and password reset complete the
flow. Guest sessions can ask questions but cannot upload files.

![BuildifyLabs sign-in](screenshots/ui-signin.png)

### Overview

`/app` is the working dashboard: a personalized hero, quota and dataset KPIs,
a getting-started checklist, recent insights, and one-click quick asks that
pre-fill the chat composer.

![BuildifyLabs overview](screenshots/ui-overview.png)

### Chat workspace

`/app/chat` is the principal screen. It combines:

- A collapsible history rail with day-grouped conversations.
- A stream where user questions, answers, clarifications, notices, and live
  pipeline states share one reading column.
- An auto-growing composer with source-scope selection, document filtering,
  file upload, quota visibility, and keyboard-friendly send.
- A read-only presentation deck for a focused view of an answer's visuals.

Both the global navigation and the chat history rail can be hidden
(`Ctrl/⌘+Shift+B` and `Ctrl/⌘+B`) for a full-width focus mode.

![BuildifyLabs chat answer](screenshots/ui-chat.png)

### Datasets

`/app/data` is the full-page version of upload management: drag-and-drop,
CSV/XLSX/PDF validation, per-file status, failure reasons, and a preview table
for the first rows of a dataset.

### Reports

`/app/reports` stores charts pinned from any chat answer. A pinned report keeps
its original visual, subtitle, and provenance-ready context so a good answer
does not disappear into scrollback.

### Activity

`/app/activity` is the honest usage view: rolling-window quota, lifetime quota,
conversation counts, and the system notices that appeared in chat. The client
displays these states; the backend remains the authority that enforces them.

## Feature highlights

### Data ingestion

| Capability | Detail |
|---|---|
| Formats | `.csv`, `.xlsx`, `.pdf` |
| Validation | Extension + MIME double-check, 0-byte rejection, per-plan size caps |
| Guest policy | Guests cannot upload; the upload affordance is absent, never disabled |
| CSV/XLSX | Defensive cleaning and parsing into a per-user queryable table |
| PDF | Text extraction, chunking, keyword retrieval, and a configurable document-QA kill switch |
| Storage | Local disk by default (`UPLOAD_DIR`); storage layer is isolated for object-store swap |
| Preview | `GET /files/{id}/preview` returns a bounded first-rows table or document preview |

### Questions to answers

1. **Rewrite** — follow-up questions are made standalone with conversation
   context.
2. **Generate** — schema-aware SQL is generated for the user's own table.
3. **Sanitize** — `sqlglot` parses the statement and enforces read-only,
   single-statement, user-scoped queries.
4. **Execute** — the deterministic executor runs the query and formats the
   result.
5. **Explain** — the model narrates only what the evidence supports.
6. **Visualize** — the response carries one of seven visual types, with a
   deterministic visual guarantee so an answer never arrives chart-less when a
   chart is possible.

### Visual answer cards

The frozen visual contract supports:

`metric` · `graph` · `table` · `comparison` · `insight` · `alert` · `status`

Charts are rendered with Recharts from real values. A normal answer can combine
several cards, a takeaway line, follow-up chips, an expanded process trace, and
a trust footer.

### Evidence and trust

- **Show the query** — the exact SQL opens in the answer's trust footer.
- **Confidence** — a bounded score with a plain-language label.
- **Sources and rows** — source receipts and raw row slices instead of vague
  citations.
- **Clarifications** — when the question is ambiguous, the assistant asks
  instead of guessing.
- **Flagging** — any answer can be flagged for review through `POST /chat/flag`.
- **Streaming states** — cold start, thinking, searching, and judging are named
  states, not an infinite spinner.

### Live web and document context

The composer's scope selector supports **Your data**, **Live web**, and **Both**.
The backend ranks evidence from multiple providers, budgets the context, caches
live-web results, and returns numbered citations. If live web is disabled by the
`ENABLE_LIVE_WEB_SCOPE` flag, the answer falls back honestly to the user's data
rather than silently pretending to search.

PDF evidence can be narrowed with the document picker; an empty selection means
all of the user's PDFs.

### Conversations and workspace

- Day-grouped history with active-thread highlighting.
- New chat and thread restoration.
- Responsive shell: sidebar on desktop, bottom tabs on mobile, overlay drawer on
  narrow chat.
- Global quick switcher (`Ctrl/⌘+K`).
- Theme toggle with light, dark, and system modes.
- Toasts, account dialogs, plan badge, quota chip, and keyboard focus states.

## Architecture

```text
┌────────────────────────────────────────────────────────────────────┐
│ React 19 + Vite + TypeScript                                      │
│                                                                    │
│ Landing · Auth · App shell · Chat · Data · Reports · Activity      │
│ Zustand stores · React Router · Recharts · plain CSS design system │
└───────────────────────────────┬────────────────────────────────────┘
                                │ HTTPS / JSON / SSE
┌───────────────────────────────▼────────────────────────────────────┐
│ FastAPI (async)                                                    │
│                                                                    │
│ /auth  /files  /chat  /chat/stream  /chat/flag  /contact  /health  │
│ JWT auth · rate limiting · quota middleware · upload validation    │
│                                                                    │
│ ┌──────────────────────── AI pipeline ───────────────────────────┐ │
│ │ query rewrite → intent/route → SQL generate → SQL sanitize     │ │
│ │      → execute → documents / live web → judge → visuals        │ │
│ │      → narrate → trust fields                                  │ │
│ └────────────────────────────────────────────────────────────────┘ │
└───────────────┬──────────────────────────────┬─────────────────────┘
                │                              │
      PostgreSQL (Neon) + pgvector        Groq / HuggingFace
      users · files · query logs          Tavily / Firecrawl / Wikipedia
      document chunks · embeddings        Yahoo Finance / FRED
```

### Request lifecycle

**Upload**

```text
browser → POST /files/upload → extension/MIME/size validation
        → parse + clean → per-user data table (CSV/XLSX)
        → extract + chunk (PDF) → 202 accepted
        → GET /files shows processing → completed / failed
```

**Question**

```text
composer → POST /chat/stream
         → auth + quota check
         → rewrite follow-up with thread context
         → generate + sanitize SQL
         → execute against the user's data
         → retrieve document/web evidence when the scope asks for it
         → validate the structured output
         → stream stage updates and answer deltas
         → final PipelineOutput replaces the live prose
```

The unary `POST /chat` endpoint returns the same structured result without the
SSE stream, which keeps scripted clients and tests simple.

### Frontend architecture

- **Feature folders** keep each screen's components, store, tests, and CSS
  together (`features/chat`, `features/auth`, etc.).
- **Zustand** powers auth, chat threads, quota, reports, source scope, document
  filters, theme, and shell navigation.
- **`src/api/*`** is the only HTTP seam; components never call `fetch` directly.
- **`src/lib/http.ts`** owns the base URL, Bearer token injection, typed errors,
  timeout behaviour, and SSE URL helpers.
- **Token storage** keeps the access token in memory and the refresh token in
  `localStorage`, with automatic refresh before expiry.
- **Visual contract** lives in `src/lib/schemas/visuals.ts`, shared by runtime
  guards and TypeScript narrowing.
- **Plain CSS tokens** in `src/index.css`; the app-wide visual layer lives in
  `src/features/app-skin.css`; the landing page is scoped in
  `features/landing/landing.css`.

### Backend architecture

| Area | Modules |
|---|---|
| API routes | `app/routes/auth.py`, `files.py`, `chat.py`, `contact.py` |
| Middleware | JWT auth, quota / rate limiting, upload validation |
| Auth | Email, Google ID token verification, guest, JWT token service, email verification / reset |
| Data | CSV/XLSX parser, defensive canonicalization, SQL executor, PDF parser, per-user storage |
| AI pipeline | `app/services/llm/pipeline/*` — prompting, routing, SQL generation, grounding, visuals, figures, judge, guarantee, history, run loop |
| Evidence | Tavily / Firecrawl / Wikipedia / Yahoo Finance / FRED adapters, ranking, context budget, map-reduce summarizer, Redis-or-memory cache |
| Persistence | Async SQLAlchemy models, Alembic migrations, pgvector document chunks |
| Database | PostgreSQL via Neon (pgvector extension), Alembic migrations |

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite 8, React Router 7, Zustand 5, Recharts 3, lucide-react, plain CSS |
| UI testing | Vitest 4, Testing Library, jsdom, user-event |
| Backend | FastAPI, Uvicorn, Pydantic v2 / pydantic-settings, async SQLAlchemy 2, asyncpg |
| Database | PostgreSQL (Neon), Alembic, pgvector |
| AI | Groq (multi-key rotation), HuggingFace fallback, Tavily + Firecrawl web evidence |
| Auth | Stateless JWT (`python-jose`), bcrypt (`passlib`), Google Identity Services |
| Email | `aiosmtplib` SMTP for verification, reset, and contact forms |
| SQL safety | `sqlglot` AST parsing and validation |
| Parsing | pandas, openpyxl, pypdf |
| CI | GitHub Actions — backend pytest; frontend lint, test, build |
| Deployment | Vercel (frontend SPA rewrite), Docker on AWS App Runner / ECS / EC2 (backend) |

## Repository layout

```text
buildifylabs/
├── Frontend/
│   ├── src/
│   │   ├── api/                 # auth, files, chat HTTP seams
│   │   ├── components/          # shared UI + 7 visual renderers
│   │   ├── features/
│   │   │   ├── activity/        # usage + history
│   │   │   ├── auth/            # signin/signup/reset/verify/guards
│   │   │   ├── chat/            # workspace, stream, composer, pickers
│   │   │   ├── data/            # datasets page
│   │   │   ├── landing/         # public marketing page
│   │   │   ├── overview/        # /app dashboard
│   │   │   ├── reports/         # pinned reports
│   │   │   └── shell/           # app frame + command palette
│   │   ├── hooks/               # auth refresh, media queries, clock
│   │   ├── lib/                 # http, tokens, theme, schemas
│   │   ├── types/               # API + domain contracts
│   │   ├── App.tsx              # routes
│   │   ├── index.css            # design tokens
│   │   └── main.tsx
│   ├── public/                  # logo, illustration, favicons
│   └── vercel.json              # SPA fallback
├── Backend/
│   ├── app/
│   │   ├── routes/              # auth, files, chat, contact
│   │   ├── services/            # auth, data, llm, web search
│   │   ├── middlewares/         # auth, quota, upload validation
│   │   ├── schemas/             # Pydantic request/response models
│   │   ├── db/                  # async engine + models
│   │   └── main.py
│   ├── alembic/                 # migrations
│   ├── tests/                   # 34 backend test modules
│   ├── evals/                   # pipeline evaluation fixtures/results
│   ├── Dockerfile
│   └── requirements*.txt
├── specs/                       # product + engineering specs
├── screenshots/                 # UI references and e2e captures
└── .github/workflows/ci.yml
```

## Getting started

### Prerequisites

- **Python 3.12+** (CI uses 3.12)
- **Node 22+** (CI uses 22)
- **PostgreSQL** with the `vector` extension enabled:
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;
  ```
- Optional: Redis for shared live-web caching, Groq key(s), Google OAuth client
  ID, SMTP credentials, Firecrawl/Tavily keys

### 1. Backend

```bash
cd Backend

python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Runtime + test tooling (pytest is intentionally not a production dependency)
pip install -r requirements-dev.txt

cp .env.example .env
# edit .env — see the environment reference below

alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Verify it is alive:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"1.2.0"}
```

`/health` probes the database, so a broken `DATABASE_URL` returns `503` instead
of pretending the service is healthy. Interactive API docs are at
`http://localhost:8000/docs`.

> The repo's `.venv` may point at a newer Python than CI. If `pytest` is missing,
> install `requirements-dev.txt` into a fresh 3.12 virtualenv; do not assume the
> system Python has pandas/pypdf/groq installed.

### 2. Frontend

```bash
cd Frontend
npm install
cp .env.example .env.local        # Vite reads .env.local
npm run dev                       # http://localhost:5173
```

Point the app at the backend:

```env
VITE_API_BASE_URL=http://localhost:8000
```

Optional:

```env
VITE_GOOGLE_CLIENT_ID=...         # Google sign-in is hidden when unset
VITE_GA_MEASUREMENT_ID=...        # GA4 pageviews are a no-op when unset
VITE_RAZORPAY_KEY_ID=...          # payments are currently paused/mocked
```

If Vite picks another port, update the backend's `ALLOWED_ORIGIN` /
`FRONTEND_URL` to match, or open the printed URL exactly.

### 3. First run checklist

1. Open `http://localhost:5173`.
2. Continue as guest or create an account.
3. Upload a CSV/XLSX from `/app/data` (not available to guests).
4. Ask a question in `/app/chat` such as *“Why did revenue change last week?”*
5. Open **Show the query**, pin the answer to **Reports**, and try the
   read-only presentation view.

## Environment reference

All backend settings are loaded from `Backend/.env` through
`pydantic-settings`. Start from `Backend/.env.example`.

### Required backend values

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async SQLAlchemy URL, e.g. `postgresql+asyncpg://user:pass@host:5432/db` |
| `JWT_SECRET` | Shared signing secret; use a long random value |
| `FRONTEND_URL` | Frontend origin used in email links |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `EMAIL_FROM` | Verification, reset, and contact mail |
| `CONTACT_FORM_RECIPIENT_EMAIL` | Recipient for the contact form |
| `GOOGLE_CLIENT_ID` | Google ID-token audience for server verification |
| `GROQ_MODEL` | Primary model name |

### Commonly tuned values

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` enables HSTS |
| `ALLOWED_ORIGIN` | `http://localhost:5173` | Comma-separated list or JSON array |
| `DATABASE_SSL` | `true` | Set `false` for local Postgres without TLS |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token TTL |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token TTL |
| `ENABLE_LIVE_WEB_SCOPE` | `true` | Kill switch; falls back honestly to own data |
| `ENABLE_DOCUMENT_QA` | `true` | PDF retrieval kill switch |
| `FIRECRAWL_API_KEY` | — | Managed search/extraction |
| `WEB_SEARCH_API_KEY` | — | Tavily evidence provider |
| `FRED_API_KEY` | — | Macro series adapter |
| `REDIS_URL` | — | Shared live-web cache; memory fallback otherwise |
| `UPLOAD_DIR` | `data/uploads` | Local upload storage |
| `TRUST_PROXY_HEADERS` | `false` | Enable only behind a trusted proxy that overwrites `X-Forwarded-For` |
| `LOGIN_RATE_LIMIT` / `VERIFY_EMAIL_RATE_LIMIT` / `GUEST_RATE_LIMIT` / `CONTACT_RATE_LIMIT` | `5` / `3` / `10` / `5` | Per-process abuse limits |

Quota limits live in `Backend/app/utils/usage.py` (rolling 6-hour window plus a
lifetime cap) and are mirrored for display in
`Frontend/src/features/chat/quota-store.ts`. The backend is the enforcement
authority; changing the numbers in one place is not enough.

## API reference

Base URL: `http://localhost:8000`. Authenticated routes expect
`Authorization: Bearer <access_token>`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | No | Service banner |
| `GET` | `/health` | No | Liveness + DB readiness (`503` when the DB probe fails) |
| `POST` | `/auth/signup` | No | Create account, issue token pair |
| `POST` | `/auth/signin` | No | Email/password sign-in |
| `POST` | `/auth/guest` | No | Device-id guest session |
| `POST` | `/auth/google` | No | Verify a Google ID token and sign in |
| `POST` | `/auth/refresh` | No | Exchange a refresh token for a new pair |
| `GET` | `/auth/verify-email` | No | Consume an email-verification token |
| `POST` | `/auth/resend-verification` | No | Resend verification mail |
| `POST` | `/auth/forgot-password` | No | Begin reset flow |
| `POST` | `/auth/reset-password` | No | Consume reset token and change password |
| `POST` | `/files/upload` | Yes | Validate + accept a CSV/XLSX/PDF (`202`) |
| `GET` | `/files` | Yes | List the caller's uploads |
| `GET` | `/files/{id}` | Yes | Fetch one upload |
| `GET` | `/files/{id}/preview` | Yes | Bounded rows / document preview |
| `DELETE` | `/files/{id}` | Yes | Delete an upload |
| `POST` | `/chat` | Yes | Ask a question, get a structured answer |
| `POST` | `/chat/stream` | Yes | Same answer over SSE with stage + text deltas |
| `POST` | `/chat/flag` | Yes | Flag an answer for review |
| `POST` | `/contact` | No | Rate-limited contact / lead form |

Structured answers use the `PipelineOutput` contract in
`Frontend/src/types/chat.ts`: answer prose, visuals, insights, root causes,
recommendations, confidence, clarification, SQL, preview rows, query-log ID,
thinking steps, and follow-ups. Quota failures return `429` with a top-level
JSON body that distinguishes the rolling-window case from the lifetime cap.

## Testing and quality

### Commands

```bash
# Frontend: lint, unit/component tests, production build
cd Frontend
npm run lint
npm test -- --run
npm run build

# Backend: full suite (install requirements-dev.txt first)
cd Backend
.venv/bin/python -m pytest -q
```

### Current checks

| Check | Status |
|---|---|
| Frontend tests | **36 files / 200 tests passing** |
| Frontend lint | Clean |
| Frontend build | Passing (`tsc -b && vite build`) |
| Backend tests | **34 test modules**; run with the project interpreter |
| CI | Backend pytest + frontend lint, test, build on every push/PR |

Frontend coverage includes routing/guards, auth screens, chat streaming and
message types, composer controls, quota states, upload popovers, each visual
renderer, the landing page, overview, datasets, reports, activity, theme store,
token storage, and HTTP error handling.

### Testing philosophy

- The backend contract is the source of truth; frontend tests mock at the
  `src/api/*` seam rather than reaching into components.
- Quota and auth states are tested as distinct outcomes, not collapsed into a
  generic error.
- Visual renderers degrade to `UnknownVisualCard` on an unrecognized type; the
  UI never crashes on a contract surprise.
- Accessibility is part of the acceptance surface: real labels, roles,
  `aria-expanded`, focus-visible states, and reduced-motion handling.

## Deployment

### Frontend (Vercel)

`Frontend/vercel.json` rewrites every path to `/index.html` so client-side
routes survive a refresh. Set:

- Build command: `npm run build`
- Output directory: `dist`
- Env: `VITE_API_BASE_URL` (required), `VITE_GOOGLE_CLIENT_ID` (optional),
  `VITE_GA_MEASUREMENT_ID` (optional)

### Backend (Docker / App Runner / ECS / EC2)

The Dockerfile:

- uses Python 3.12 slim,
- installs runtime dependencies only,
- copies `app/` + Alembic,
- runs as an unprivileged user,
- runs `alembic upgrade head` before Uvicorn,
- exposes a DB-aware `/health` check.

Set the required environment values, point `ALLOWED_ORIGIN` at the deployed
frontend, and keep `DATABASE_SSL=true` for Neon. For more than one replica, run
migrations as a separate deploy step so replicas do not race on the Alembic
version row.

Uploads are local to the container by default; move `UPLOAD_DIR` to a mounted
volume or replace the storage layer with object storage before relying on
multi-instance deployments.

## Design system

The UI intentionally shares one visual language across marketing, auth, and the
product:

- **Type:** Outfit (300–600) loaded from Google Fonts; App fallback stack for
  offline or blocked-font environments.
- **Palette:** soft lilac paper, white cards, ink text, one violet accent
  (`#6d4df2`) warmed by peach highlights. Dark mode is a deep plum variant.
- **Shape:** 10 / 14 / 20 / 28 px radii, pill controls, hairline borders, soft
  elevation.
- **Surfaces:** landing uses oversized centered type and floating panels; the
  product uses the same language at a calmer density.
- **Tokens:** role names live in `Frontend/src/index.css`; feature CSS never
  invents a second palette. `Frontend/src/features/app-skin.css` applies the
  shared product skin last so layouts stay owned by their features.
- **Themes:** light, dark, and system. The pre-paint guard in `index.html`
  prevents a wrong-theme flash, and `data-theme` overrides keep both themes
  consistent.
- **Accessibility:** labelled controls, focus rings, `aria-pressed` /
  `aria-expanded` states, minimum 12px type, reduced-motion rules, and
  keyboard shortcuts.

The landing page art direction lives in `features/landing/landing.css`; chat,
shell, datasets, reports, activity, and auth are unified by `app-skin.css` and
`features/auth/auth.css`.

## Security and privacy

- **JWT sessions:** short-lived access tokens stay in memory; refresh tokens are
  persisted in `localStorage` and rotated through `/auth/refresh`.
- **Per-user isolation:** SQL is scoped to the authenticated user's table, and
  uploads are listed and fetched by owner only.
- **SQL safety:** `sqlglot` enforces read-only, single-statement queries; raw
  model output is never executed directly.
- **Upload hardening:** allowed extensions, exact MIME matching, empty-file
  rejection, per-plan size caps, guest upload denial, and bounded previews.
- **Abuse controls:** login, verification, guest, contact, and question quotas
  are rate-limited; proxy headers are ignored unless explicitly trusted.
- **Response headers:** nosniff, frame denial, referrer policy, permissions
  policy, and HSTS in production.
- **Secrets:** provider keys and the JWT secret live in environment variables,
  never in the client bundle.
- **AI honesty:** ambiguous questions are clarified, unsupported claims are
  hedged, confidence is bounded, and answers carry evidence rather than
  pretending certainty.

For production, add your own retention policy for uploads, database backups,
SMTP deliverability controls, and object storage encryption.

## Spec-driven development

`specs/` is the product intent, written module by module. This README describes
the implementation as it exists in the repository; where the two differ, the
code and tests win, and the spec should be updated.

Useful starting points:

| Spec | Topic |
|---|---|
| `specs/00-overview.md` | Product thesis, module status map, system architecture |
| `specs/01-authentication.md` | Email/Google/guest auth, verification, reset |
| `specs/02-plan-quota-enforcement.md` | Rolling window + lifetime quota |
| `specs/04-file-upload-ingestion.md` | Validation, CSV/XLSX/PDF ingestion |
| `specs/05-query-sql-safety.md` | NL→SQL safety sandbox |
| `specs/06-ai-insight-pipeline.md` | Structured answers + visuals |
| `specs/07-news-context-module.md` | Live-web scope and evidence |
| `specs/10-trust-safety-compliance.md` | Trust posture and compliance |
| `specs/12-llm-orchestration.md` | Multi-provider orchestration plan |
| `specs/14-chat-workspace-ui-design.md` | Chat workspace UI contract |

Backend and frontend contributor notes live in `Backend/CLAUDE.md` and
`Frontend/CLAUDE.md`.

### Honest status notes

- CSV/XLSX ingestion and PDF document QA are implemented.
- Payments are present as a paused/mocked surface, not a production billing
  flow.
- The graph knowledge store is deferred.
- The multi-provider LLM cascade in spec 12 is a target; the current pipeline
  uses Groq with HuggingFace fallback.
- Older planning docs may mention a Next.js migration. The shipping frontend is
  the React 19 + Vite app in `Frontend/`.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `/health` returns `503` | Database unreachable or migrations not applied; check `DATABASE_URL`, `DATABASE_SSL`, and run `alembic upgrade head` |
| `relation "vector" does not exist` | Enable pgvector: `CREATE EXTENSION IF NOT EXISTS vector;` |
| Browser CORS error | `ALLOWED_ORIGIN` must exactly match the frontend origin, including port |
| Google button missing | `VITE_GOOGLE_CLIENT_ID` is unset; the UI intentionally hides Google sign-in |
| “Streaming is not supported” | The response was not a readable stream; verify `/chat/stream` is reachable and not buffered by a proxy |
| Every question returns quota errors | The caller hit the rolling-window or lifetime cap; check `Backend/app/utils/usage.py` and the quota store |
| Upload stuck in `processing` | Inspect backend logs for parser/PDF failure; failed files keep their reason in the dataset row |
| Guest upload button missing | Intentional: guest accounts cannot upload |
| `pytest: No module named pytest` | Activate a Python 3.12 venv and install `requirements-dev.txt`; production requirements are runtime-only |
| Fonts look different | Google Fonts may be blocked; the app falls back to the system stack without changing layout tokens |

## Contributing

1. Read the relevant spec and the contributor guide for the area you are
   changing.
2. Keep the backend contract and frontend types in sync — `src/types/` mirrors
   the Pydantic schemas.
3. Add or update tests at the same seam as the behaviour.
4. Run `npm run lint`, `npm test -- --run`, `npm run build` in `Frontend/` and
   `python -m pytest -q` in `Backend/` before opening a pull request.
5. Do not commit `.env` files, API keys, uploaded data, or generated caches.

CI mirrors these checks on every push and pull request.

---

<div align="center">
  <strong>BuildifyLabs</strong> · Ask your business data anything.<br />
  <sub>© 2026 BuildifyLabs. All rights reserved.</sub>
</div>
