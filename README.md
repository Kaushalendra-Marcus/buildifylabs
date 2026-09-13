<div align="center">
  <img src="Frontend/public/logo.png" width="120" alt="BuildifyLabs fox logo" />
  <h1>BuildifyLabs</h1>
  <p><strong>Ask your business data anything. Get answers you can trace.</strong></p>
  <p>Upload a spreadsheet, ask in plain English, and get the right chart, the why behind it, and the exact query it came from. No dashboards to build. No SQL to write.</p>
</div>

---

## What it is

BuildifyLabs is an AI Business Intelligence copilot for small and medium businesses. Upload your
business data (CSV, XLSX, or PDF), ask questions in plain English, and get back charts,
root-cause explanations, and recommendations — every answer carrying the exact SQL query and
raw data behind it, so nothing is a black box.

- **4 free questions, no card to start. Your data stays yours.**

## Features

- **File upload & ingestion** — CSV/XLSX land in a per-user queryable data table; PDFs are chunked, embedded, and retrieved per question (pgvector).
- **Plain-English → SQL** — schema-aware generation with AST-based safety checks and structural per-user scoping (a query can never read another user's rows).
- **Seven real visual components** — metric, graph (line/bar/pie/area), table, comparison, insight, alert, status — synthesized deterministically from real values.
- **Live web + your data** — per-question source scope (Your data / Live web / Both) with ranked, budgeted evidence and numbered citations.
- **Forecasting & what-ifs** — deterministic trend projection and price-scenario recompute; the model narrates precomputed numbers, never its own arithmetic.
- **Conversation continuity** — follow-ups resolve against prior turns; the history rail restores past transcripts.
- **Trust built in** — show-the-query, confidence meter, flag-this-answer, hedged causal language, clarification quick-picks instead of guesses.
- **Auth & quota** — email, Google, and guest sign-in; rolling-window + lifetime quota with honest 429 states.

## Tech stack

| Layer | Stack |
|---|---|
| Frontend | React 19 + TypeScript + Vite, Zustand, React Router, Recharts, plain CSS design tokens, Vitest + RTL |
| Backend | FastAPI (async) + Uvicorn, PostgreSQL (Neon) + SQLAlchemy async + Alembic, pgvector |
| AI | Groq (primary) → HuggingFace (fallback), HF Inference embeddings |
| Auth | Stateless JWT (access + refresh), Google Identity Services |

## Monorepo layout

```
buildifylabs/
├── Frontend/   # React app (Vite, :5173) — workspace, chat, visuals, landing page
├── Backend/    # FastAPI API (:8000) — auth, quota, upload, /chat pipeline
└── specs/      # Product specs, one file per module (authoritative requirements)
```

## Quickstart

### Prerequisites

- Python 3.12+, Node 18+, a PostgreSQL database with the `pgvector` extension (`CREATE EXTENSION vector;`)

### 1. Backend

```bash
cd Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` (required vars):

```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
JWT_SECRET=your-secret
FRONTEND_URL=http://localhost:5173
SMTP_HOST=...  SMTP_PORT=...  SMTP_USER=...  SMTP_PASS=...  EMAIL_FROM=...
CONTACT_FORM_RECIPIENT_EMAIL=you@example.com
GOOGLE_CLIENT_ID=...
GROQ_API_KEY=...  GROQ_MODEL=...
HF_API_KEY=...
```

Then migrate and run:

```bash
alembic upgrade head
uvicorn app.main:app --reload   # http://localhost:8000
```

### 2. Frontend

```bash
cd Frontend
npm install
```

Optional `.env` (sensible defaults built in):

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_GOOGLE_CLIENT_ID=...
```

```bash
npm run dev       # http://localhost:5173 (backend CORS is locked to this port)
```

## Verification

```bash
# Backend (from Backend/)
python -m pytest

# Frontend (from Frontend/)
npm test -- --run
npm run build
npm run lint
```

## API overview

| Endpoint | Description |
|---|---|
| `POST /chat` | Ask a question (`query`, `source_scope`, `thread_id`) → structured answer with visuals |
| `POST /chat/stream` | Same answer as SSE (stage pings + streaming prose + final result) |
| `POST /chat/flag` | Flag an answer on its `QueryLogs` row |
| `POST /files/upload` | Upload CSV/XLSX/PDF (202 + status lifecycle) |
| `GET /files`, `GET /files/{id}` | List / fetch own uploads |
| `POST /contact` | Lead-capture form (lifetime-quota flow) |
| Auth | Signup, signin, Google, guest, verify-email, forgot/reset-password |

## Docs

- `specs/` — product requirements per module (`00-overview.md` is the index)
- `Backend/CLAUDE.md`, `Frontend/CLAUDE.md` — contributor orientation per app
- `Backend/docs/` — conventions, known gaps, and module notes

---

<div align="center">© 2026 BuildifyLabs</div>
