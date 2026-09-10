# Visual-Empty Fix — Run Status (this run)

Production bug: correct prose answer, zero component visuals, ASCII chart drawn in text.

## Root cause

- `decompose_comparison_query` extracted phantom entities ("Cost Of Top", "Output Prize Both") → comparison gate applied → validated-history branch found no history and returned naked before figure synthesis ran.
- No prompt rule forbade ASCII/text charts in prose.

## Completed

- [x] `comparison.py`: `_INDICATOR_TOKENS` += top/inputs/outputs/prizes/both/either/neither → zero entities, gate off; 8 snippet money figures attribute → comparison + bar + figures/products tables + timeline (0 → 5 visuals, verified with repro script)
- [x] `langchain_pipeline.py::ensure_visuals`: validated branch early-returns only when it synthesized something, else falls through (final provenance filter + BLOCKED path unchanged; real-entity-no-evidence still ships no graph/comparison — pinned by test)
- [x] `SYSTEM_PROMPT`: NO TEXT CHARTS rule (components carry numbers, prose carries takeaway; LLM already instructed to choose among the 7 types + fill props)
- [x] Tests: ghost case F, `TestPricingComparisonVisuals` (2), trust-boundary pin, prompt marker (4 new total)
- [x] Verification: backend **638 passed** (up from 634), frontend **124 passed** (untouched)

## Files touched

- `Backend/app/services/data/comparison.py`, `Backend/app/services/llm/langchain_pipeline.py`
- `Backend/tests/test_ghost_entities_regression.py`, `Backend/tests/test_pipeline_contract.py`

---

# Web-Evidence Flow Completion — Run Status

Follow-up to the B7 Evidence Hardening run below: closes the remaining gaps
against the desired flow (LLM rewrites to 3–4 queries → search → scrape →
batch → LLM answer → 1–7 data-driven visuals).

## Completed (this run)

- [x] **Phase 1 — Query framing 3→4** (`query_rewriter.py`, `web_search.py`)
  - `MAX_REWRITE_QUERIES=4` (prompt: 1–4 queries), `MAX_SEARCH_QUERIES=4` so the 4th query is searched
  - `test_query_rewriter.py`: cap test updated (`test_query_list_capped_at_four`)
- [x] **Phase 2 — Whole-pool budgeting** (`web_search.py` final assembly)
  - Structured evidence deduped as (text, source) pairs (keeps citation alignment), placed first, then ranked snippets; one greedy fit guarantees TOTAL ≤ `MAX_EVIDENCE_CONTEXT_CHARS`
  - `test_context_budget.py`: + structured-survival integration test (market text first, total ≤ budget, aligned)
- [x] **Phase 3 — Deep reads** (`config.py` new `MAX_DEEP_READ_URLS=5`, `web_search.py`)
  - Recommendation intent full-reads up to 5 URLs (was hardcoded 3); summarization still applies per page
  - `test_context_budget.py`: + deep-read cap test (5 extract calls, deep content leads, aligned)
- [x] **Phase 4 — Visual ceiling 6→7** (`config.py`, `test_pipeline_contract.py`)
  - `MAX_SYNTHESIZED_VISUALS=7`; ceiling fixture (+ fundamentals) yields 7, monkeypatched 2 yields 2
- [x] **Phase 6 — Verification**
  - Backend: `python3 -m pytest` from `Backend/` — **634 passed** (up from 632, no regressions)
  - Frontend: `npm test` ✅ (**124 passed**, untouched)

## Deferred (separate architecture, needs your decision)

- **Phase 5 — PDF/XLSX unstructured document QA** (parse → chunk → embed → Pinecone → retrieve → synthesize; `specs/04`/`specs/08`): multi-week initiative touching upload, storage, and a new retrieval path — not bundled here. CSV upload → per-user table → NL→SQL path is unchanged and working.
- Multi-LLM provider cascade (`specs/12`).

---

# B7 Evidence Hardening — Run Status (previous)

Implementation of `implementation-plan-b7-evidence-hardening.md`, Phases 0–6, followed literally in order.

## Completed

- [x] **Phase 0 — Documentation reconciliation (no behavior change)**
  - `specs/00-overview.md`: row 7 → partial note; architecture line → `Frontend (React 19 + Vite + react-router)`
  - `specs/07-news-context-module.md`: removed shipped FR3 from still-open list, added FR4 note
  - `Backend/app/routes/chat.py` + `Backend/app/schemas/chat.py`: docstrings now state live_web/both implemented + tested
  - `Backend/CLAUDE.md`: Redis marked wired/optional; chat supports live_web/both
  - `Frontend/CLAUDE.md`: TODO(phase-6) marker added
  - `Frontend/src/features/chat/Composer.tsx`: top comment updated (no tooltip change yet)

- [x] **Phase 1 — Config additions** (`Backend/app/config.py`)
  - `MAX_EVIDENCE_CONTEXT_CHARS=12000`, `SUMMARIZE_TRIGGER_CHARS=6000`, `MAX_SYNTHESIZED_VISUALS=6`, `ENABLE_LIVE_WEB_SCOPE=True`

- [x] **Phase 2 — Visual guarantee ceiling** (`langchain_pipeline.py::ensure_visuals`)
  - 3 literal slices → `get_settings().MAX_SYNTHESIZED_VISUALS`
  - New tests in `test_pipeline_contract.py::TestMaxSynthesizedVisuals` (2 tests: 6-visual fixture, monkeypatched ceiling of 2)

- [x] **Phase 3 — Evidence ranking + budget**
  - New `Backend/app/services/llm/context_budget.py` (estimate/rank/fit, fail-closed, citation-aligned)
  - `web_search.py`: ranked + budgeted snippet pool, `research_notes` trim disclosure
  - New `Backend/tests/test_context_budget.py` (8 tests incl. over-budget search_web integration)

- [x] **Phase 4 — Long-source summarization**
  - New `Backend/app/services/llm/evidence_summarizer.py` (map-reduce, short-text fast path, truncation fallback)
  - `_tavily_extract()`: summarizes over `SUMMARIZE_TRIGGER_CHARS` (recommendation deep-read inherits)
  - New `Backend/tests/test_evidence_summarizer.py` (5 tests incl. Tavily integration)

- [x] **Phase 5 — Scope-disagreement clarification (FR4)**
  - `query_rewriter.py`: `EXTERNAL_CONTEXT_RE` + `wants_external_context()`
  - `chat.py`: `EXTERNAL_CONTEXT_CLARIFICATION_QUESTION`, FR4 quick-pick with `_same_question` anti-repeat, `effective_scope` ("Yes..." → "both", scope never mutated, all branches use `effective_scope`)
  - New tests: 3 in `test_chat_api.py::TestExternalContextClarification`, 2 in `test_query_rewriter.py::TestWantsExternalContext`

- [x] **Phase 6 — Live rollout**
  - `chat.py`: `ENABLE_LIVE_WEB_SCOPE` kill-switch on `effective_scope` (honest own_data-only fallback)
  - `Composer.tsx`: tooltip copy updated; comment updated
  - `composer.css`: checked — no live-web-specific disabled style existed, nothing removed
  - `Composer.test.tsx`: new tooltip title assertions + header comment
  - `Frontend/CLAUDE.md`: live + feature-flag description (TODO resolved)
  - `specs/07` → ✅ Implemented; `specs/00` row 7 → ✅ Implemented
  - `STATUS.md`: Completed-tasks bullet added

## Verification (B7 run)

- Backend: `python3 -m pytest` from `Backend/` — **632 passed** (baseline 612; +20 new, no regressions)
- Frontend: `npm run build` ✅, `npm run lint` ✅, `npm test` ✅ (**124 passed**, unchanged — only assertions added to existing test)

## Explicitly out of scope (per plan §7)

- Document/PDF QA over uploads (chunking + embeddings + vector store)
- Multi-LLM provider cascade (`specs/12`)
