<div align="center">
  <img src="Frontend/public/logo.png" width="120" alt="BuildifyLabs fox logo" />
  <h1>BuildifyLabs</h1>
  <p><strong>Ask your business data anything. Get answers you can trace.</strong></p>
  <p>Upload a spreadsheet, ask in plain English, and get the right chart, the why behind it, and the exact query it came from.</p>
</div>

---

## What it is

BuildifyLabs is an AI business intelligence copilot for small and medium businesses. Upload your
data, ask questions in plain English, and get back charts, explanations, and recommendations.
Every answer ships with the exact SQL query and raw data behind it, so nothing is a black box.

4 free questions. No card to start. Your data stays yours.

## Features

- **File upload:** CSV and XLSX go to a per-user data table. PDFs are chunked, embedded, and retrieved per question.
- **Plain English to SQL:** schema-aware generation with safety checks and per-user scoping.
- **Visual answers:** metric, graph, table, comparison, insight, alert, and status cards built from real values.
- **Live web + your data:** per-question source scope with ranked evidence and numbered citations.
- **Forecasting and what-ifs:** deterministic projections the model narrates but never computes.
- **Conversations:** follow-ups keep context, and the history rail restores past chats.
- **Trust:** show-the-query, confidence meter, flag-this-answer, and clarifications instead of guesses.
- **Auth and quota:** email, Google, and guest sign-in with rolling-window and lifetime limits.

## Tech stack

| Layer | Stack |
|---|---|
| Frontend | React 19, TypeScript, Vite, Zustand, React Router, Recharts, Vitest |
| Backend | FastAPI, PostgreSQL (Neon), SQLAlchemy, Alembic, pgvector |
| AI | Groq with HuggingFace fallback and embeddings |
| Auth | Stateless JWT, Google Identity Services |

## Layout

```
buildifylabs/
├── Frontend/   # React app on :5173
├── Backend/    # FastAPI API on :8000
└── specs/      # Product specs, one file per module
```

## Quickstart

Prerequisites: Python 3.12+, Node 18+, PostgreSQL with pgvector (`CREATE EXTENSION vector;`).

**Backend**

```bash
cd Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` with the required vars:

```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
JWT_SECRET=your-secret
FRONTEND_URL=http://localhost:5173
SMTP_HOST=... SMTP_PORT=... SMTP_USER=... SMTP_PASS=... EMAIL_FROM=...
CONTACT_FORM_RECIPIENT_EMAIL=you@example.com
GOOGLE_CLIENT_ID=...
GROQ_API_KEY=... GROQ_MODEL=...
HF_API_KEY=...
```

```bash
alembic upgrade head
uvicorn app.main:app --reload   # http://localhost:8000
```

**Frontend**

```bash
cd Frontend
npm install
npm run dev   # http://localhost:5173
```

Optional `.env`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_GOOGLE_CLIENT_ID=...
```

## Verify

```bash
# Backend
cd Backend && python -m pytest

# Frontend
cd Frontend && npm test -- --run && npm run build && npm run lint
```

## API

| Endpoint | Description |
|---|---|
| `POST /chat` | Ask a question, get a structured answer with visuals |
| `POST /chat/stream` | Same answer over SSE with streaming prose |
| `POST /chat/flag` | Flag an answer for review |
| `POST /files/upload` | Upload CSV, XLSX, or PDF |
| `GET /files`, `GET /files/{id}` | List and fetch your uploads |
| Auth | Signup, signin, Google, guest, verify-email, password reset |

## Docs

- `specs/`: product requirements (`00-overview.md` is the index)
- `Backend/CLAUDE.md`, `Frontend/CLAUDE.md`: contributor guides

---

<div align="center">© 2026 BuildifyLabs</div>
