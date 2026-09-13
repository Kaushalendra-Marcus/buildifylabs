# Implementation Plan — Conversation Continuity, Document QA & Forecasting

**Companion to `implementation-plan-master.md`, `implementation-plan-b7-evidence-hardening.md`, and
`implementation-plan-reliability-hardening.md`.** Written after a full audit of the live codebase
(not from the specs alone) — every finding below is traced to an exact file/function, and every fix
is designed to reuse existing conventions rather than invent new ones, because a coding agent
follows precedent far more reliably than it invents good architecture from a paragraph of prose.

**Read this whole document before writing any code.** The four parts touch some of the same files
(`chat.py`, `chat-store.ts`) — doing them out of order re-does work.

---

## 0. Audit summary (what's broken, evidence, why it matters)

| # | Issue | Where | Severity |
|---|---|---|---|
| A | Follow-up questions lose context — SQL generation and web-search query rewriting only ever see the current turn's raw text, never the previous turn's | `sql_generator.py::build_sql_prompt`, `query_rewriter.py::rewrite_search_queries` | **High** — breaks the single most common conversational pattern ("and last quarter?", "break that down by region") |
| B | Frontend never sends `thread_id`, and clicking a past conversation in the History Rail doesn't restore it | `Composer.tsx` line 90, `chat-store.ts` | **High** — makes (A)'s eventual fix partially moot, and the rail is a visible, clickable dead end |
| C | PDF/XLSX uploads are accepted but never actually parsed — only CSV works | `app/services/data/parser.py`, `specs/04-file-upload-ingestion.md` | **High** — contradicts the product's own upload copy ("Add a CSV, PDF, or spreadsheet") |
| D | Forecasting (specs/11 §3.2) is specified and shown on the marketing landing page, but not implemented anywhere in the pipeline | `specs/11-prediction-and-calculation.md` (acceptance criteria unchecked) | **Medium** — no user-facing lie today (the real chat never claims to forecast), but it's advertised on `/` |

All four are real, independently reproducible, and independently fixable. They're bundled into one
plan because A and B share files and a shared identifier (the conversation id becomes the thread id),
and because a coding agent works best executing one coherent plan top to bottom rather than four
disconnected ones.

---

## Non-negotiable constraints (apply to every part below)

1. **Never touch a giant pipeline file more than the minimum.** `pipeline/run.py` (68KB),
   `pipeline/prompting.py` (17KB), `web_search.py` (106KB) have absorbed a long history of careful,
   narrow patches. Every edit below is stated as an exact anchor ("find this line, add these lines
   after it") — do not refactor surrounding code while you're in there.
2. **Reuse existing primitives before writing new ones.** This codebase already has
   `build_canonical_query`, `context_budget.py`, `evidence_summarizer.py`, the 7-visual-type
   contract, and the compute-in-code/narrate-with-LLM pattern (`stats.py`). Every part below reuses
   at least one of these instead of inventing a parallel mechanism.
3. **Fail soft, never fail loud.** Every new code path wraps external calls (HTTP, DB) in
   `try/except` that degrades to "skip this evidence source" — exactly like `search_web()` and
   `evidence_summarizer.py` already do. A broken embedding call must never 500 the whole `/chat`
   request.
4. **Every new/changed behavior ships with tests in the same phase**, not a follow-up. Backend:
   `pytest` from `Backend/`. Frontend: `npm test` from `Frontend/`. Both suites must stay 100% green;
   counts only go up.
5. **Config additions go in `app/config.py` as `Field` entries with sane defaults**, never bare
   `os.environ.get()` calls scattered in modules — matching every existing setting.
6. **Migrations chain off the current head** (`b4code0000` — check
   `Backend/alembic/versions/` for a newer head before writing the `down_revision`; if one exists,
   chain off that instead).

---

# PART A — Conversation context continuity

### A0. Root cause (confirmed by reading the code, not guessing)

`sql_generator.py::build_sql_prompt(user_query, schema)` takes only the *current* turn's text. The
richer prior-turn machinery (`prior_data`, `prior_research_state`, `chart_from_prior`) exists in
`pipeline/run.py`, but it only runs **after** SQL has already executed — it can rescue a "chart that"
follow-up when fresh SQL returns zero rows, but it cannot help a follow-up that generates *some*
(wrong, context-blind) SQL, which is the common case. `query_rewriter.py::rewrite_search_queries` has
the identical shape: it accepts `prior_clarification` (for the one clarification-answer case) but no
general `prior_query`.

The codebase already has the right tool for this: `app/services/data/canonical.py::build_canonical_query(original, prior_clarification, followup)`
— a pure, deterministic text-merge helper, already used by `merge_clarification_context()` in
`chat.py`, just scoped narrowly to "the user is answering a clarification I just asked." We are not
inventing a new mechanism — we are widening an existing one and, critically, making the *consuming*
prompts (SQL generation, search-query rewriting) aware of "previous question" context in the first
place, since today they don't even have a parameter for it.

**Design choice — do not blind-merge every follow-up.** Concatenating the previous turn's text into
every new turn (even a genuinely unrelated new question) would pollute fresh questions with stale
context. Instead of a hand-rolled "is this related?" classifier (unreliable for arbitrary CSV
columns — `decompose_comparison_query`, the tool `canonical_query_changed()` relies on, is tuned for
company/stock comparisons, not generic business data), **pass the previous question to the LLM as
labeled, optional context and let it decide relevance** — exactly the pattern `build_prompt()`
already uses for `prior_data_section` at the narration stage. We're moving that same pattern one
stage earlier, to where it actually needs to change what gets fetched.

### A1. `app/services/llm/sql_generator.py` — SQL prompt gets prior-turn context

```python
def build_sql_prompt(
    user_query: str,
    schema: Optional[str] = None,
    prior_query: Optional[str] = None,
) -> str:
    schema = schema or DEFAULT_DATABASE_SCHEMA
    prior_section = ""
    if prior_query and prior_query.strip():
        prior_section = f"""
    Previous question in this conversation (context only):
    {prior_query.strip()}

    If the question below is a follow-up that refers back to the previous one
    (pronouns like "that"/"it", or an incomplete phrase like "what about next
    month" or "break that down by region"), resolve it using the previous
    question's subject, metric, and time period. If the question below is a
    complete, self-contained question on its own topic, ignore the previous
    question entirely.
"""
    return f"""
    Database Schema:
    {schema}
    {prior_section}
    User Query:
    {user_query}
    Generate a safe PostgreSQL query.
"""
```

`prior_query` is optional and defaults to `None` — every existing call site (including all current
tests) keeps working unchanged.

### A2. `app/services/llm/query_rewriter.py` — search rewriting gets the same context

Open the file and find `rewrite_search_queries(query, prior_clarification: Optional[str] = None, ...)`. Add a
sibling parameter `prior_query: Optional[str] = None`, and extend the existing `context_lines`
block (it already does this for `prior_clarification` — mirror that exact pattern):

```python
if prior_query:
    context_lines.append(
        f"Previous question in this conversation (context only, resolve "
        f"follow-up references like 'that'/'it'/'last one' against it — "
        f"ignore it completely if this question is self-contained): {prior_query}"
    )
```

### A3. `app/services/web_search.py` — thread `prior_query` through `search_web`

`search_web()` already accepts `prior_clarification`. Add `prior_query: Optional[str] = None` next to
it in the signature, and pass it straight through at the existing `rewrite_search_queries(...)` call
site (~line 1878):

```python
framed = await rewrite_search_queries(
    query,
    prior_clarification=prior_clarification,
    prior_query=prior_query,
    ...
)
```

### A4. `app/routes/chat.py` — wire it up in `_answer_request`

Two call sites need the new parameter. `prior_query` is already loaded by `_load_prior_context()` —
it's just never passed anywhere. Guard against double-injection: when the clarification merge already
fired (`plan_query != request.query`), `plan_query` already *contains* the prior question's text, so
don't also pass raw `prior_query` — that would show the model the same context twice, confusingly
labeled two different ways.

```python
# after the existing clarification-merge block that sets plan_query:
sql_prior_context = prior_query if plan_query == request.query else None
```

Then:
- SQL prompt call site: `sql_prompt = build_sql_prompt(plan_query, schema, prior_query=sql_prior_context)`
- Inside `_search_branch()`: add `prior_query=sql_prior_context` to the `search_web(...)` call.

### A5. Same pattern for `plan_tools` (tool routing), lower priority

`plan_tools(plan_query, source_scope=..., prior_clarification=prior_clarification, ...)` has the
identical shape and would benefit from the same `prior_query` context (e.g. "and its macro data
too" needs to know what "its" was). Apply the identical pattern — add the parameter, thread it
through, add it to the prompt inside `plan_tools`'s own prompt-building step — but treat this as a
follow-on inside Phase A, not a blocker: `plan_tools` already fails open (`[]` = no opinion, dispatch
falls back to deterministic predicates), so getting this one right matters less than A1/A2.

### A6. Tests to add (`Backend/tests/`)

- `test_sql_generator.py` (or wherever `build_sql_prompt` is tested): a call with `prior_query` set
  produces a prompt containing both the schema and the labeled prior-question section; a call
  without it is byte-identical to today's output (regression guard).
- `test_query_rewriter.py`: same shape — `prior_query` appears in `context_lines` only when passed.
- `test_chat_api.py`: an end-to-end case — ask a question, then ask a bare follow-up ("what about
  last month") in the same `thread_id`, assert the *second* SQL-generation call's prompt (mock
  `generate_response` and inspect the call args) contains the first question's text. This is the
  regression test that actually proves the bug is fixed, not just that the new parameter exists.

### Definition of done — Part A

- [ ] `build_sql_prompt`, `rewrite_search_queries`, `search_web`, `plan_tools` accept optional
      `prior_query`; all default to `None` and are backward compatible.
- [ ] `chat.py` passes `prior_query` (never double-injected alongside a clarification merge).
- [ ] New tests pass; full existing suite stays green.

---

# PART B — History Rail actually restores past conversations, and thread_id ships

### B0. Root cause

`Composer.tsx` line 90 sends `{ query: text, source_scope: scope }` to `sendQuery` — **`thread_id` is
never in the payload**, so the backend's `_thread_id_for()` always falls back to the literal string
`"default"` for every request from every user, across every "New chat" click, forever. Separately,
`chat-store.ts`'s `ChatMessage` types carry no `conversationId` field, so `selectConversation(id)`
(in `HistoryRail.tsx`) has nothing to filter the visible `messages` array by — it only changes which
item is visually highlighted (the code's own comment admits this: *"Visual selection only until
per-thread transcripts land"*).

**Design choice — one id, two jobs.** The frontend already generates a stable per-conversation id
(`ChatConversation.id`, format `m-N`) the moment the first message of a conversation is sent, and
keeps reusing it for every message in that conversation until "New chat" is clicked. This is exactly
what a `thread_id` needs to be. Rather than inventing a second identifier, **send the existing
conversation id as `thread_id` on every request**, and **tag every message with the conversation id
it belongs to** so the rail can actually filter by it. This fixes both halves of Part B with one id.

### B1. `Frontend/src/features/chat/chat-store.ts` — tag messages with their conversation

Add `conversationId: string` to `UserChatMessage`, `AssistantChatMessage`, and `SystemChatMessage`.
In `addUserMessage`, `addAssistantMessage`, `addSystemNotice`, stamp the message with
`state.activeConversationId` (the id resolved/created at the top of `addUserMessage` — the two other
actions read the current `activeConversationId` from state at call time). Example for
`addAssistantMessage`:

```ts
addAssistantMessage: (output) =>
  set((state) => ({
    messages: [
      ...state.messages,
      {
        id: makeId(),
        role: 'assistant',
        output,
        createdAt: Date.now(),
        conversationId: state.activeConversationId ?? 'default',
      },
    ],
  })),
```

Add a derived selector (not stored state — computed at read time) that the message stream will use:

```ts
export function messagesForConversation(
  messages: ChatMessage[],
  conversationId: string | null,
): ChatMessage[] {
  if (conversationId === null) return messages;
  return messages.filter((m) => m.conversationId === conversationId);
}
```

`newChat()` must generate and set a **new** `activeConversationId` immediately (today it just nulls
it out and waits for the first message) so a freshly-opened "New chat" has a real thread id ready
before the first request goes out — otherwise the first message of a new conversation still has
nowhere to send `thread_id` from. Smallest change: keep `activeConversationId: null` on `newChat()`
exactly as today (the existing `addUserMessage` logic already creates a fresh id on the first message
when `activeConversationId === null`) — no change needed here, just confirming this path is already
correct once messages carry `conversationId`.

### B2. `Frontend/src/features/chat/messages/MessageStream.tsx` — render only the active conversation

Find wherever `MessageStream` reads `messages` from the store and filter through
`messagesForConversation(messages, activeConversationId)` before rendering. `selectConversation(id)`
in the store already sets `activeConversationId` — no change needed there, it was correct, it just had
nothing to filter with until now.

### B3. `Frontend/src/api/chat.ts` + `Composer.tsx` — send `thread_id`

`ChatRequest` (backend schema, already has `thread_id: Optional[str]`) needs the frontend type
(`Frontend/src/types/chat.ts` or wherever `ChatRequest` is declared) to include it too if it doesn't
already. In `Composer.tsx`, change the `sendQuery` call:

```ts
const output = await sendQuery(
  {
    query: text,
    source_scope: scope,
    thread_id: useChatStore.getState().activeConversationId ?? undefined,
  },
  ...
);
```

Read via `getState()` (not a hook) if `Composer.tsx` doesn't already subscribe to
`activeConversationId` — check the existing imports first; if it already has a `useChatStore` hook
call for something else, add a normal selector instead of `getState()` for consistency with the rest
of the file.

### B4. Persisted-storage version bump

`chat-store.ts`'s `persist` config has `version: 1`. Adding `conversationId` to message shapes is an
additive field — old persisted messages without it will have `conversationId: undefined`, which
`messagesForConversation`'s filter would exclude from every real conversation. Either:
- bump to `version: 2` with a `migrate` function that stamps every existing message with the id of
  the **first** conversation in the persisted `conversations` array (best-effort, avoids losing
  history on upgrade), or
- accept that pre-upgrade history becomes unreachable via the rail (still present in `messages` for
  the 50-message cap, just not clickable) and document it in the STATUS.md entry for this phase.

Recommend the `migrate` function — it's a dozen lines and avoids a silent data-loss-feeling bug for
anyone testing this locally with existing localStorage state.

### B5. Tests to add (`Frontend/`)

- `chat-store.test.ts`: sending two messages in conversation A, starting a new chat, sending one
  message in conversation B, then `messagesForConversation(messages, A.id)` returns exactly A's two
  messages.
- `HistoryRail.test.tsx` (or extend existing): clicking a past conversation updates
  `activeConversationId`, and (via a shallow render with a mocked store) the visible message list
  changes to match.
- `Composer.test.tsx`: assert the `sendQuery` mock is called with a `thread_id` matching the current
  `activeConversationId`.
- `chat-persist.test.ts`: the migration test — seed `version: 1` shaped localStorage data, load the
  store, assert messages gained a `conversationId`.

### Definition of done — Part B

- [ ] Every `/chat` request carries a real, stable `thread_id` per conversation.
- [ ] Clicking a past conversation in the rail actually shows that conversation's messages.
- [ ] Existing localStorage state migrates without visible data loss.
- [ ] New tests pass; full existing suite stays green.

---

# PART C — Document QA: PDF and XLSX actually work

### C0. Decisions (read before writing any code)

**Vector store: pgvector on the existing Neon Postgres, not Pinecone.** `config.py` has unused
`PINECONE_API_KEY`/`PINECONE_ENVIRONMENT` fields and `file_upload.py`'s `pinecone_namespace` column
was reserved for this — but Neon supports the `pgvector` extension natively on every plan, no add-on
or paid tier, `CREATE EXTENSION vector;` once per database. Using it avoids a second vendor, a second
API key to rotate, and a second outage surface, and keeps everything in the one DB this app already
depends on. If a future scale need outgrows pgvector, the swap seam is `vector_store.py` (§C4) — the
rest of the pipeline never talks to storage directly.

**Embeddings: Hugging Face Inference API, reusing the existing `HF_API_KEY`.** No new local ML
dependency (no `torch`, no `sentence-transformers` package — keeps the container light). Endpoint:
`POST https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-MiniLM-L6-v2`
(384-dim output) — this exact model name already sits unused in `config.py`'s
`HUGGINGFACE_MODEL_PATH` field, confirming it was the intended choice.

**XLSX is tabular — reuse the CSV pipeline almost entirely (do this phase first, it's cheap).** PDF
is not tabular — it needs the new chunk/embed/retrieve pipeline.

**Multiple PDFs coexist per user (append, don't replace).** CSV/XLSX keep today's "one active file,
new upload replaces it" behavior unchanged — a spreadsheet is naturally "the one dataset," a set of
PDF reports naturally accumulates.

**Budget discipline: document evidence gets its own reserved sub-budget, always included, ranked
before web evidence** — not thrown into the same relevance-ranked pool as web snippets. Your own
uploaded document is inherently more trustworthy than a generic web result; it must never be crowded
out by a coincidentally-well-scored web snippet. See §C7.

### C1. Config additions (`Backend/app/config.py`)

```python
ENABLE_DOCUMENT_QA: bool = Field(True, env="ENABLE_DOCUMENT_QA")

DOCUMENT_CHUNK_CHARS: int = 1000
DOCUMENT_CHUNK_OVERLAP_CHARS: int = 150
MAX_DOCUMENT_CHARS: int = 200_000       # safety cap on extracted PDF text before chunking
MAX_CHUNKS_PER_FILE: int = 400          # independent backstop

DOCUMENT_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
DOCUMENT_EMBEDDING_DIM: int = 384

MAX_DOCUMENT_CHUNKS_PER_QUERY: int = 6  # top-K retrieval
MAX_DOCUMENT_CONTEXT_CHARS: int = 4000  # reserved sub-budget, see §C7
```

`ENABLE_DOCUMENT_QA` is the kill switch, matching the existing `ENABLE_LIVE_WEB_SCOPE` pattern
exactly — when false, PDF uploads still succeed (parsed and chunked) but retrieval at query time is
skipped, same fail-honest shape as the live-web kill switch.

### C2. Dependencies (`Backend/requirements.txt`)

```
# C1: XLSX read support for pandas (tabular ingestion, same path as CSV)
openpyxl
# C2: PDF text extraction (lightweight, no native deps)
pypdf
# C4: pgvector Python/SQLAlchemy integration
pgvector
```

### C3. Phase 1 — XLSX ingestion (do first: fast, low-risk, reuses everything)

Open `Backend/app/services/data/parser.py`. Find `ingest_file()`'s extension dispatch (it currently
handles `.csv` and raises/fails on anything else). Add an `.xlsx` branch that reads the sheet into
the identical `DataFrame` shape CSV already produces, then falls through to the **same**
`clean_dataframe()` + `upsert_user_table()` calls CSV uses — do not duplicate that logic:

```python
elif ext == ".xlsx":
    import io
    df = pd.read_excel(io.BytesIO(contents), engine="openpyxl")
```

Everything after that line (cleaning, table creation, column typing) is unchanged — this is the
entire diff. `file_validator.py` already accepts `.xlsx` with the correct MIME
(`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) — no middleware change needed.

**Tests:** `tests/test_parser.py` (or wherever CSV ingestion is tested) — add a mirror test with a
small in-memory `.xlsx` built via `openpyxl`/`pandas.DataFrame.to_excel`, asserting it lands in the
per-user table with the same columns/rows a CSV would.

### C4. Phase 2 — PDF text extraction + chunking

New file `Backend/app/services/data/pdf_parser.py`:

```python
"""PDF text extraction + chunking for document QA (Part C).

Pure functions, no DB/network — `document_ingest.py` orchestrates storage.
Fails loud on genuinely unreadable PDFs (caller/route already catches and
sets FileUpload.status="failed", matching the existing CSV/XLSX contract);
degrades gracefully on partial extraction (some pages readable, some not)
by skipping unreadable pages rather than failing the whole file.
"""
import logging
from typing import List

from pypdf import PdfReader

from app.config import get_settings

logger = logging.getLogger(__name__)


def extract_pdf_text(contents: bytes) -> str:
    """Extract running text from every readable page, capped by settings.

    Scanned/image-only PDFs yield little or no text — this is a known
    limitation (OCR is out of scope, see the plan's "Not in this plan"),
    surfaced honestly: a PDF that extracts to near-empty text should fail
    ingestion with a clear reason, not silently create zero chunks.
    """
    settings = get_settings()
    reader = PdfReader(io.BytesIO(contents))
    parts: List[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning(f"Skipping unreadable PDF page: {exc}")
            continue
        if not text.strip():
            continue
        parts.append(text)
        total += len(text)
        if total >= settings.MAX_DOCUMENT_CHARS:
            break
    return "\n\n".join(parts)[: settings.MAX_DOCUMENT_CHARS]


def chunk_text(text: str) -> List[str]:
    """Fixed-size character chunking with overlap (simple, deterministic —
    no sentence/paragraph-boundary detection needed for v1; overlap covers
    the case where a fact straddles a chunk boundary)."""
    settings = get_settings()
    size = settings.DOCUMENT_CHUNK_CHARS
    overlap = settings.DOCUMENT_CHUNK_OVERLAP_CHARS
    text = (text or "").strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(text) and len(chunks) < settings.MAX_CHUNKS_PER_FILE:
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start <= 0:
            break
    return chunks
```

(add `import io` at the top alongside the others)

**Tests:** generate a tiny synthetic PDF at test time — add `reportlab` (or `fpdf2`, it's lighter —
prefer `fpdf2`) as a **dev-only** dependency (`requirements-dev.txt` if one exists, otherwise a
`pytest` fixture-local import guarded by `pytest.importorskip("fpdf")`) purely to build a fixture PDF
with known text in `tests/`. Assert `extract_pdf_text` recovers that text, and `chunk_text` produces
overlapping chunks of the configured size on a long synthetic string (no PDF needed for this half —
plain string in, list of strings out).

### C5. Phase 3 — Embeddings (`Backend/app/services/data/embeddings.py`)

```python
"""HF Inference API embeddings (Part C). Mirrors groq_service.py's
hf_fallback() shape: httpx call, fail-soft, one clear error type."""
import logging
from typing import List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Raised when the embedding call fails outright — callers decide
    whether that means "fail this file's ingestion" (ingestion time) or
    "skip document evidence for this query" (query time, fail soft)."""


def _hf_url(model: str) -> str:
    return f"https://api-inference.huggingface.co/pipeline/feature-extraction/{model}"


async def embed_texts(texts: List[str]) -> List[Optional[List[float]]]:
    """Embed a batch of strings. Returns one vector per input, same order;
    an individual failed item is None (caller skips that chunk), a total
    request failure raises EmbeddingError (caller decides fail-file vs
    fail-soft per the docstring above)."""
    settings = get_settings()
    if not texts:
        return []
    url = _hf_url(settings.DOCUMENT_EMBEDDING_MODEL)
    headers = {"Authorization": f"Bearer {settings.HF_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url, headers=headers,
                json={"inputs": texts, "options": {"wait_for_model": True}},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning(f"Embedding request failed: {exc}")
        raise EmbeddingError(str(exc)) from exc
    if not isinstance(data, list):
        raise EmbeddingError(f"Unexpected embedding response shape: {type(data)}")
    out: List[Optional[List[float]]] = []
    for item in data:
        if isinstance(item, list) and item and isinstance(item[0], (int, float)):
            out.append([float(v) for v in item])
        elif isinstance(item, list):
            # Some deployments return per-token vectors; mean-pool to one.
            try:
                dim = len(item[0])
                pooled = [sum(tok[i] for tok in item) / len(item) for i in range(dim)]
                out.append(pooled)
            except Exception:
                out.append(None)
        else:
            out.append(None)
    while len(out) < len(texts):
        out.append(None)
    return out


async def embed_text(text: str) -> Optional[List[float]]:
    """Single-string convenience wrapper (query-time embedding)."""
    result = await embed_texts([text])
    return result[0] if result else None
```

**Tests:** mock `httpx.AsyncClient.post` — assert (a) a well-formed batch response returns one
384-length vector per input, (b) a mean-pooling fallback path works for the per-token response
shape, (c) an HTTP error raises `EmbeddingError` and never crashes the caller unhandled.

### C6. Phase 4 — pgvector storage

**Model** — new file `Backend/app/db/models/document_chunk.py`:

```python
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, UUID, DateTime, String, ForeignKey, Integer, Text
from sqlalchemy.sql import func
from app.db.database import Base
import uuid


class DocumentChunk(Base):
    """One embedded chunk of an uploaded PDF (Part C). No ORM relationship
    on User/FileUpload — vector_store.py queries this table directly by
    user_id/file_id, matching the plain-FK style QueryLogs already uses."""
    __tablename__ = "document_chunks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("file_uploads.id"), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

Register it in `Backend/app/db/models/__init__.py` (same reason every other model is there — the
declarative registry needs it imported before mappers configure):

```python
from app.db.models.document_chunk import DocumentChunk
# ...and add "DocumentChunk" to __all__
```

**Migration** — `Backend/alembic/versions/c1code0000_document_chunks.py` (check
`Backend/alembic/versions/` first for a head newer than `b4code0000`; chain `down_revision`
accordingly if so):

```python
"""add document_chunks table + pgvector extension

Part C (document QA): PDF text lands here as embedded chunks, retrieved by
cosine similarity scoped to user_id at query time. XLSX/CSV keep using the
per-user SQL table unchanged — this is additive, not a replacement.

Revision ID: c1code0000
Revises: b4code0000
Create Date: 2026-09-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "c1code0000"
down_revision: Union[str, Sequence[str], None] = "b4code0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("file_id", sa.UUID(as_uuid=True), sa.ForeignKey("file_uploads.id"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_document_chunks_user_id", "document_chunks", ["user_id"])
    op.create_index("ix_document_chunks_file_id", "document_chunks", ["file_id"])
    # ivfflat needs rows to train on well; at MVP scale a plain scan is fine,
    # but create the index now so it's not a forgotten follow-up at scale:
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.drop_index("ix_document_chunks_file_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_user_id", table_name="document_chunks")
    op.drop_table("document_chunks")
```

**CRUD + retrieval** — new file `Backend/app/services/data/vector_store.py`:

```python
"""pgvector CRUD + retrieval (Part C). Postgres-only (the `<=>` cosine
operator and the Vector column type don't exist on SQLite) — see the test
note below for how this is still covered without requiring every dev/CI run
to have a real Postgres instance."""
import logging
import uuid
from typing import List, Optional, Tuple

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.document_chunk import DocumentChunk
from app.services.data.embeddings import EmbeddingError, embed_text, embed_texts

logger = logging.getLogger(__name__)

DOCUMENT_SOURCE_PROVIDER = "your_documents"  # matches the frontend literal, see §C8


async def store_chunks(
    db: AsyncSession, user_id, file_id, file_name: str, chunks: List[str],
) -> int:
    """Embed and store every chunk. Returns the count actually stored (a
    chunk whose embedding call failed is skipped, not fatal to the file —
    matches evidence_summarizer.py's per-item fail-soft philosophy)."""
    if not chunks:
        return 0
    try:
        vectors = await embed_texts(chunks)
    except EmbeddingError as exc:
        # Ingestion-time: the whole file has no usable evidence -> fail it,
        # matching the existing FileUpload status="failed" contract.
        raise
    stored = 0
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        if vector is None:
            continue
        db.add(DocumentChunk(
            id=uuid.uuid4(), user_id=user_id, file_id=file_id, file_name=file_name,
            chunk_index=index, content=chunk, embedding=vector,
        ))
        stored += 1
    await db.commit()
    return stored


async def delete_file_chunks(db: AsyncSession, user_id, file_id) -> None:
    await db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.user_id == user_id, DocumentChunk.file_id == file_id,
        )
    )
    await db.commit()


async def search_chunks(
    db: AsyncSession, user_id, query_embedding: List[float], top_k: int,
) -> List[dict]:
    """Cosine-similarity top-K, scoped to user_id at the SQL level (never
    filtered after the fact — same tenant-isolation discipline as
    executor.py::assert_user_scoped). Postgres/pgvector only."""
    result = await db.execute(
        text(
            "SELECT content, file_name, chunk_index, created_at, "
            "1 - (embedding <=> :qvec) AS score "
            "FROM document_chunks WHERE user_id = :uid "
            "ORDER BY embedding <=> :qvec LIMIT :k"
        ),
        {"qvec": str(query_embedding), "uid": str(user_id), "k": top_k},
    )
    return [dict(row) for row in result.mappings()]


async def user_has_document_chunks(db: AsyncSession, user_id) -> bool:
    result = await db.execute(
        select(DocumentChunk.id).where(DocumentChunk.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def retrieve_document_evidence(
    db: AsyncSession, user_id, query_text: str,
) -> Tuple[List[str], List[dict]]:
    """Orchestration entry point chat.py calls. Fails soft end to end: any
    failure (embedding, DB) returns ([], []) and is logged, never raised —
    document evidence is a bonus channel, not a required one. Returns
    (texts, source_dicts) in the exact `news_context`/`web_sources` shape
    (see §C8) so chat.py can merge them with zero new PipelineOutput fields."""
    settings = get_settings()
    if not settings.ENABLE_DOCUMENT_QA:
        return [], []
    try:
        if not await user_has_document_chunks(db, user_id):
            return [], []
        query_vector = await embed_text(query_text)
        if query_vector is None:
            return [], []
        rows = await search_chunks(
            db, user_id, query_vector, settings.MAX_DOCUMENT_CHUNKS_PER_QUERY,
        )
    except Exception as exc:
        logger.warning(f"Document retrieval skipped (fail-soft): {exc}")
        return [], []
    texts = [row["content"] for row in rows]
    sources = [
        {
            "title": row["file_name"],
            "url": "",
            "provider": DOCUMENT_SOURCE_PROVIDER,
            "retrieved_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "published_date": str(row["created_at"])[:10] if row.get("created_at") else None,
            "score": row.get("score"),
        }
        for row in rows
    ]
    return texts, sources
```

(replace the two `__import__("datetime")` calls with a normal `from datetime import datetime,
timezone` import at the top — written inline above only so this snippet is copy-pasteable without
losing the import in translation; **do not actually ship the `__import__` form**, use a real import.)

**Testing note — the one piece of this plan that needs real Postgres.** `Backend/tests/conftest.py`
sets a *dummy* `DATABASE_URL`; the actual test suite (`test_chat_api.py` etc.) runs against an
in-memory/file-backed **SQLite** engine. SQLite has no `vector` type and no `<=>` operator — the
`Vector(384)` column and `search_chunks()`'s raw SQL simply cannot run there. Do not attempt a
SQLite-compatible fallback implementation (a second code path here is exactly the kind of
"never a second mechanism" risk this codebase's own conventions warn against). Instead:

- `store_chunks`, `retrieve_document_evidence`'s *orchestration* logic (embedding calls, fail-soft
  branches, the `ENABLE_DOCUMENT_QA` kill switch) is fully unit-testable by mocking `embed_texts`/
  `embed_text` and a mocked `AsyncSession` — no real Postgres needed for these.
- `search_chunks`'s actual SQL correctness (does the `<=>` query really return nearest neighbors,
  does the `WHERE user_id` filter really isolate tenants) needs one small, explicitly-gated
  integration test file: `tests/test_vector_store_integration.py`, guarded by
  `@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="needs a real Postgres with pgvector")`.
  Document in that file's docstring exactly how to run it locally (point `TEST_POSTGRES_URL` at a
  scratch Neon branch with `CREATE EXTENSION vector;` already run). The rest of the suite is
  unaffected and stays fast/dependency-free.
- Everything downstream in `chat.py` (§C7) is tested the same way the existing `search_web()`
  integration already is: mock `retrieve_document_evidence` at the function boundary.

### C7. Phase 5 — wire PDF into upload + budget documents correctly

**`app/services/data/parser.py`** — the `.pdf` branch currently fails with "not supported yet."
Replace it:

```python
elif ext == ".pdf":
    from app.services.data.pdf_parser import extract_pdf_text, chunk_text
    from app.services.data.vector_store import store_chunks

    text = extract_pdf_text(contents)
    if not text.strip():
        raise ValueError(
            "No readable text found in this PDF. Scanned/image-only PDFs "
            "aren't supported yet — try a text-based PDF."
        )
    chunks = chunk_text(text)
    stored = await store_chunks(db, user_id, upload_id, filename, chunks)
    if stored == 0:
        raise ValueError("Couldn't process this PDF's content — please try again.")
    return f"vector:{upload_id}"  # storage reference, mirrors the table-name convention
```

Check `ingest_file()`'s actual signature — it may need `upload_id` added as a parameter if it isn't
already passed (CSV/XLSX's `upsert_user_table` path is deterministic from `user_id` alone and may
not currently receive it). Confirm against `routes/files.py`'s call:
`table_name = await parser.ingest_file(db, user.id, file.filename, contents)` — **`upload_id` is
not currently passed**. Add it: `parser.ingest_file(db, user.id, upload_id, file.filename, contents)`,
and thread the extra parameter through `ingest_file`'s signature (CSV/XLSX branches simply ignore it).

**`app/routes/chat.py::_answer_request`** — add the document-evidence branch, run concurrently with
the existing SQL and web branches:

```python
async def _document_branch():
    if effective_scope not in ("own_data", "both"):
        return [], []
    from app.services.data.vector_store import retrieve_document_evidence
    return await retrieve_document_evidence(db, user_id, plan_query)
```

Add it to the existing `asyncio.gather(...)` alongside `_execute_branch()` and `_search_branch()`:

```python
exec_result, search_result, doc_result = await asyncio.gather(
    _execute_branch() if table_name is not None else asyncio.sleep(0, result=[]),
    _search_branch(),
    _document_branch(),
)
document_context, document_sources = doc_result
```

**Budget merge — reserved sub-budget for documents, then remaining budget for web (§C0's design
decision).** Find where `news_context`/`web_sources` get built from `search_result` (the
`if search_result is not None:` block already in `_answer_request`). After that block, before the
`run_pipeline(...)` call, add:

```python
from app.services.llm.context_budget import fit_pairs_to_budget

_settings = get_settings()
_doc_budget = min(_settings.MAX_DOCUMENT_CONTEXT_CHARS, _settings.MAX_EVIDENCE_CONTEXT_CHARS)
document_context, document_sources, _ = fit_pairs_to_budget(
    document_context, document_sources, _doc_budget
)
_remaining_budget = max(
    0, _settings.MAX_EVIDENCE_CONTEXT_CHARS - sum(len(t) for t in document_context)
)
news_context, web_sources, _ = fit_pairs_to_budget(
    news_context or [], web_sources or [], _remaining_budget
)
news_context = document_context + news_context
web_sources = document_sources + web_sources
```

Check `fit_pairs_to_budget`'s exact signature/return shape in `context_budget.py` before pasting this
(it was written for the web-snippet pool; confirm it returns `(kept_texts, kept_sources,
dropped_count)` or adjust the unpacking to match — the plan's earlier audit read this function and
that was its shape, but re-verify against the current file before wiring it in, since this file may
have changed since the audit).

**`_user_has_data()` — already correct, no change needed.** It checks "does the user have any
`FileUpload` row with `status == "completed"`" — once a PDF successfully ingests and sets
`status="completed"`, a PDF-only user already passes this check. Confirmed by reading the function;
do not "fix" something that already works.

**New real risk this phase surfaces — guard the SQL branch for PDF-only users.** Today, `table_name =
user_data_table_name(user_id)` is set unconditionally whenever `effective_scope` includes
`own_data`/`both`, even if the user only ever uploaded a PDF (no CSV/XLSX ever created that table).
`get_table_columns()` would then return `[]`, `build_sql_prompt` would embed a schema with zero
columns, and the SQL branch would most likely produce a confusing 422 fallback message — even though
the *document* branch succeeded in parallel. Fix: only attempt the SQL branch when the table actually
has columns.

```python
if effective_scope in ("own_data", "both"):
    table_name = user_data_table_name(user_id)
    columns = await get_table_columns(db, table_name)
    if not columns:
        table_name = None  # no structured data for this user; skip SQL, keep documents/web
    else:
        schema = build_data_schema(table_name, columns)
        sql_prompt = build_sql_prompt(plan_query, schema, prior_query=sql_prior_context)
        sql_result = await generate_response(...)
        cleaned_sql = clean_sql_response(sql_result.get("content") or "")
```

The existing `_execute_branch()` already guards on `table_name is not None` in the `gather(...)` call
(`_execute_branch() if table_name is not None else asyncio.sleep(0, result=[])`) — this fix makes
that guard actually trigger correctly for a PDF-only user instead of always being true.

### C8. Phase 6 — prompting.py: label document evidence correctly, and frontend badge

**Backend — `pipeline/prompting.py`.** The citation-numbering logic (`dated_lines.append(...)`)
needs zero structural changes (documents ride the existing `news_context`/`web_sources` numbering,
per §C7's merge) — but the static label above it currently reads `"Web Search Results:"`, which
would mislead the model into narrating "according to web search results" when citing the user's own
uploaded PDF. Two small, surgical edits:

1. Change the prompt template's static heading from `Web Search Results:` to `Retrieved Evidence
   (web search and/or your uploaded documents — each item's source is tagged inline):`.
2. In the `dated_lines.append(...)` loop, tag each line with its provider:

```python
for index, snippet in enumerate(news_context, start=1):
    date = ""
    tag = ""
    if index - 1 < len(web_sources):
        source = web_sources[index - 1] or {}
        raw_date = source.get("published_date")
        if raw_date:
            date = f", {str(raw_date)[:10]}"
        if source.get("provider") == "your_documents":
            tag = ", from your uploaded document"
    dated_lines.append(f"[{index}{date}{tag}] {snippet}")
```

This is the entire prompting.py diff. `sanitize_citations()` needs no change — it only ever consumed
a plain `source_count` integer.

**Frontend — `Frontend/src/features/chat/messages/evidence.ts`.** `deriveSources()` currently treats
every `web_sources` entry as `kind: 'live-web'`. Add a third kind:

```ts
export interface DerivedSource {
  kind: 'your-data' | 'your-documents' | 'live-web';
  ...
}
```

```ts
for (const web of output.web_sources ?? []) {
  const isDocument = web.provider === 'your_documents';
  ...
  sources.push({
    kind: isDocument ? 'your-documents' : 'live-web',
    title: web.title,
    subtitle,
    detail: null,
    url: web.url || null,
  });
}
```

**`Frontend/src/features/chat/messages/DataSources.tsx`** — add a third badge count (mirroring the
existing `yourDataCount`/`liveWebCount` pattern) and a `FileText` icon (from `lucide-react`, already
a dependency) distinct from `Database` (SQL) and `Globe` (web) for document-kind source cards.

**Tests:** `evidence.test.ts` (or wherever `deriveSources` is tested) — a `web_sources` entry with
`provider: 'your_documents'` classifies as `'your-documents'`, not `'live-web'`. `DataSources.test.tsx`
renders the new badge when document sources are present.

### C9. Not in this part (explicitly out of scope)

- OCR for scanned/image-only PDFs (needs Tesseract or a cloud OCR API — separate initiative,
  `extract_pdf_text` already fails cleanly on these today rather than silently ingesting nothing).
- Structured table extraction from PDFs (pypdf gets running text only; a PDF that's mostly tables
  will extract poorly — `pdfplumber` is the upgrade path if this becomes a real complaint).
- Deleting/replacing a single uploaded PDF (there is no `DELETE /files/{id}` for *any* file type
  today — pre-existing gap, not created by this plan).
- Cross-document re-ranking (hybrid BM25+vector, a re-ranker model) — top-K cosine similarity is the
  honest v1; note it as a future quality lever if retrieval quality turns out too shallow in
  practice.

### Definition of done — Part C

- [ ] XLSX uploads parse into the per-user SQL table exactly like CSV.
- [ ] PDF uploads extract text, chunk it, embed it, and store it in `document_chunks`.
- [ ] A PDF-only user (no CSV/XLSX ever uploaded) can ask a question about their own data and get an
      answer grounded in their PDF — not a "you haven't uploaded any data" fallback, not a confusing
      SQL 422.
- [ ] Document evidence is always included (within its reserved sub-budget) and never silently
      crowded out by web evidence when both exist.
- [ ] The frontend correctly labels document-sourced citations as "your documents," not "live web."
- [ ] `ENABLE_DOCUMENT_QA=false` degrades cleanly (PDFs still ingest; retrieval is skipped).
- [ ] New tests pass (including the gated pgvector integration test, documented as needing
      `TEST_POSTGRES_URL`); full existing suite stays green.

---

# PART D — Forecasting (specs/11 §3.2)

### D0. Design — ship the honest v1, per the spec's own stated principle

specs/11 §3.2 is explicit: *"a simple method that's honestly labeled as simple is better than a
complex one that overstates its own confidence."* Use linear-regression (or simple moving-average)
extrapolation over the user's own already-fetched time-series rows — computed in Python
(`stats.py`), never by the LLM, following the exact pattern `apply_what_if()` already established for
what-if scenarios (deterministic computation in code; the LLM only narrates the precomputed result and
must quote its stated assumptions verbatim, enforced by the SYSTEM_PROMPT's existing WHAT-IF RULE —
add a parallel FORECAST RULE the same way).

**Chart rendering — do not add dashed-line styling in v1.** `GraphCard.tsx`'s `Line` components have
no `strokeDasharray` support today, and adding one is a real (if small) frontend change with its own
schema/prop surface. The honest, minimal v1: render "Actual" and "Projected" as **two separate named
datasets in the same line chart** — `GraphCard` already colors each dataset differently
(`DATASET_COLORS[index]`) and already renders a `Legend` — so "Actual" (solid, historical values,
`null`/absent for future periods) and "Projected" (solid, a different color, `null`/absent for
historical periods, with a shared last-actual-point stitched in as the bridge point so the two lines
visually connect) works with **zero GraphCard changes**. A dashed-line polish pass is a fine phase-2,
not a blocker.

### D1. `Backend/app/services/data/stats.py` — deterministic forecast function

Add alongside `apply_what_if`/`parse_what_if` (mirror their exact shape and naming convention):

```python
def is_forecast_query(text: str) -> bool:
    """Deterministic detector, same style as is_what_if_query — a regex over
    forward-looking phrasing ('forecast', 'project', 'predict', 'next
    month/quarter/year', 'going forward')."""
    import re
    return bool(re.search(
        r"\b(forecast|project(?:ion|ed)?|predict\w*|next (week|month|quarter|year)|going forward)\b",
        text or "", re.IGNORECASE,
    ))


def compute_forecast(rows: list[dict], date_col: str, value_col: str, periods_ahead: int = 1) -> Optional[dict]:
    """Linear-regression extrapolation over (date_col, value_col) pairs from
    already-fetched rows. Returns None (not a guess) when there isn't enough
    history to be meaningful (specs/11 §5: 'the pipeline should recognize
    insufficient data and say so, not force an answer') — the caller treats
    None exactly like apply_what_if's None: no forecast visual, no forecast
    claim in the narration.

    Method is linear regression on the numeric row index (not a real
    time-aware model) — honestly labeled as such in the returned dict, which
    the SYSTEM_PROMPT's FORECAST RULE requires the model to state verbatim.
    """
    import numpy as np

    series = [
        (row.get(date_col), row.get(value_col))
        for row in rows
        if row.get(date_col) is not None and row.get(value_col) is not None
    ]
    series = [(d, v) for d, v in series if isinstance(v, (int, float))]
    if len(series) < 4:
        return None
    series.sort(key=lambda pair: str(pair[0]))
    values = np.array([float(v) for _, v in series])
    x = np.arange(len(values))
    slope, intercept = np.polyfit(x, values, 1)
    next_x = len(values) + periods_ahead - 1
    projected = float(slope * next_x + intercept)
    residuals = values - (slope * x + intercept)
    std_err = float(np.std(residuals)) if len(residuals) > 1 else 0.0
    return {
        "method": "linear regression over the available history (not a seasonal model)",
        "historical_periods": len(values),
        "projected_value": round(projected, 2),
        "confidence_range": [round(projected - std_err, 2), round(projected + std_err, 2)],
        "last_actual_value": round(float(values[-1]), 2),
        "last_actual_label": str(series[-1][0]),
        "assumption": (
            "Assumes the recent trend continues unchanged — does not account for "
            "seasonality, one-off events, or external factors."
        ),
    }
```

### D2. Wire into `pipeline/run.py`, mirroring the what-if wiring exactly

Find where `is_what_if_query(plan_text)` / `apply_what_if` are called in `chat.py`/`run.py` (the
what-if wiring already read earlier in this audit: `chat.py` computes `what_if` and merges it into
`computed_numbers`). Add the identical pattern for forecast — in `chat.py`, alongside the existing
what-if block:

```python
if rows:
    try:
        if is_forecast_query(plan_query):
            date_col, value_col = infer_forecast_columns(rows)  # see below
            if date_col and value_col:
                forecast = compute_forecast(rows, date_col, value_col)
                if forecast is not None:
                    computed = {**computed, "forecast": forecast}
    except Exception as exc:
        logger.warning(f"Forecast computation skipped: {exc}")
```

`infer_forecast_columns(rows)` — a small helper in `stats.py`: pick the first column whose values
parse as dates (or an already-known date-like column name pattern — reuse whatever heuristic
`compute_statistics`/`clean_dataframe` already uses for date detection in `parser.py`/`stats.py`,
don't invent a second one) as `date_col`, and the first numeric column as `value_col`. If ambiguous
(multiple numeric columns), skip forecasting rather than guess — same "ask, don't guess" principle;
a genuinely ambiguous case can fall to the judge's `clarify` path naturally since `computed["forecast"]`
would simply be absent.

**SYSTEM_PROMPT** (`pipeline/prompts.py`) — add a `FORECAST RULE` paragraph next to the existing
`WHAT-IF RULE`, same shape: *"When `computed_numbers.forecast` is present, narrate `projected_value`
and quote `method` and `assumption` verbatim. Never state a forecast as certain — always frame it as
a projection. Never compute your own trend — only narrate the precomputed forecast."*

**Visual** — extend `decision.visual_plan`'s existing `kind` set usage (no `VISUAL_TYPES` schema
change needed — `graph` already exists) so the judge/narration step can emit a `graph` visual whose
`datasets` are `"Actual"` (historical `value_col` values, unchanged) and `"Projected"` (a leading run
of `null`s for every historical period except the last real one — which is duplicated as the bridge
point — followed by `forecast["projected_value"]`). This is a data-shaping change inside
`pipeline/visuals.py`'s existing graph-building step, not a new visual type — locate where `graph`
visuals currently get their `datasets` built from `computed_numbers`/`db_data` and add a
forecast-aware branch that only fires when `computed_numbers.get("forecast")` is present.

### D3. Tests

- `test_stats.py`: `compute_forecast` on a clean upward-trending synthetic series returns a
  `projected_value` in the expected direction with `historical_periods` matching input length;
  fewer than 4 valid points returns `None`; a series with non-numeric/missing values in some rows
  is filtered, not crashed on.
- `test_chat_api.py`: a forecast-phrased question against seeded time-series test data produces a
  `PipelineOutput` whose narration mentions "projection"/"not a fact" framing (matching the
  SYSTEM_PROMPT rule) and whose visuals include a `graph` with an "Actual"/"Projected" dataset pair.

### D4. Not in this part

- Seasonal/ARIMA-style forecasting — v1 is intentionally simple, per the spec's own stated
  philosophy; a more sophisticated method is a natural v2 once the honest-simple version is live and
  its confidence ranges have been checked against real usage.
- Forecasting over live-web/market data (only the user's own uploaded time-series data is in scope
  here — `price_history`/`financial_history` already have their own historical-gate logic in
  `pipeline/history.py`, unrelated to this).

### Definition of done — Part D

- [ ] `compute_forecast` is a pure, tested function; the LLM never computes a forecast number itself.
- [ ] A forecast-phrased question against the user's own data produces a `graph` visual with
      Actual/Projected series and a narration that states the method and a confidence range.
- [ ] Insufficient history (<4 points) degrades to no forecast, not a guess.
- [ ] specs/11 §3.2's acceptance-criteria checkbox can be checked.
- [ ] New tests pass; full existing suite stays green.

---

## Suggested execution order (across all four parts)

1. **Part A** (A1–A4) — smallest surgical fix, no new infra, immediately improves every existing
   query type. Do this first to build confidence before the bigger parts.
2. **Part B** — frontend-only, no backend dependency, pairs naturally with Part A (same `thread_id`
   concept, opposite end of the wire).
3. **Part D** (forecasting) — self-contained, no new infrastructure, reuses the proven
   compute-in-code/narrate-with-LLM pattern already established by what-if. Do this before Part C so
   the team isn't context-switching between "new infra" (pgvector) and "pure logic" work back to back.
4. **Part C** (document QA) — largest, only part needing new infrastructure (pgvector) and a new
   external call pattern (HF embeddings). Do this last, after A/B/D have built momentum with smaller,
   fully self-contained wins.

Each part is independently shippable and independently revertible — there is no hard dependency
forcing a different order, this is a recommendation for risk management, not a technical constraint.

## Global testing discipline

- Backend: `cd Backend && pytest` must stay 100% green after every phase; test count only increases.
- Frontend: `cd Frontend && npm test` must stay 100% green after every phase; test count only
  increases.
- Every new external-service call site (HF embeddings, pgvector queries) is wrapped exactly like
  existing ones (`search_web`, `groq_service.hf_fallback`) — logged warning + graceful degradation,
  never an unhandled exception reaching the route handler.
- After each part, update `Backend/docs/known-gaps.md` and this repo's `STATUS.md` following their
  existing entry format — closing the bullets this plan's audit opened, not leaving them stale for
  the next person to re-discover.

## File manifest (everything this plan touches, for a final checklist)

**New files:**
`Backend/app/services/data/pdf_parser.py`, `Backend/app/services/data/embeddings.py`,
`Backend/app/services/data/vector_store.py`, `Backend/app/db/models/document_chunk.py`,
`Backend/alembic/versions/c1code0000_document_chunks.py`,
`Backend/tests/test_vector_store_integration.py`

**Edited files:**
`Backend/app/config.py`, `Backend/requirements.txt`,
`Backend/app/services/llm/sql_generator.py`, `Backend/app/services/llm/query_rewriter.py`,
`Backend/app/services/web_search.py`, `Backend/app/routes/chat.py`,
`Backend/app/services/data/parser.py`, `Backend/app/services/data/stats.py`,
`Backend/app/services/llm/pipeline/prompting.py`, `Backend/app/services/llm/pipeline/prompts.py`,
`Backend/app/services/llm/pipeline/visuals.py`, `Backend/app/db/models/__init__.py`,
`Backend/app/routes/files.py`,
`Frontend/src/features/chat/chat-store.ts`, `Frontend/src/features/chat/Composer.tsx`,
`Frontend/src/features/chat/messages/MessageStream.tsx`,
`Frontend/src/features/chat/messages/evidence.ts`,
`Frontend/src/features/chat/messages/DataSources.tsx`, `Frontend/src/types/chat.ts` (if separate)

**Docs to update after shipping:** `specs/04-file-upload-ingestion.md`, `specs/11-prediction-and-calculation.md`,
`Backend/docs/known-gaps.md`, `Backend/CLAUDE.md`, `STATUS.md`.
