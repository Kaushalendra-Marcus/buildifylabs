# Continuity + Document QA + Forecasting — Run Status (2026-09-13)

Implementation of `implementation-plan-continuity-and-completeness.md`, Parts A–D in the
plan's suggested order (A → B → D → C), followed literally. Canonical living state is
`STATUS.md` (uppercase) — this file is the per-run log only.

## Completed

- [x] **Part A — Conversation context continuity** (DoD: all four `prior_query` params optional/`None`, wired without double-injection, suites green)
  - `sql_generator.py::build_sql_prompt(user_query, schema, prior_query=None)` — labeled prior-question section; no-prior output byte-identical (regression-guarded)
  - `query_rewriter.py::rewrite_search_queries(..., prior_query=None)` — mirrored `context_lines` pattern
  - `web_search.py::search_web(..., prior_query=None)` — threaded to the rewriter call
  - `pipeline/judge.py::plan_tools(..., prior_query=None)` — same pattern (follow-on inside Phase A, per plan)
  - `routes/chat.py` — `sql_prior_context = prior_query if plan_query == request.query else None`; passed to the SQL prompt, `search_web`, and `plan_tools`
  - Tests: `TestBuildSqlPromptPriorQuery` (3), `TestRewriteSearchQueriesPriorQuery` (1), `TestConversationContinuity` e2e (1); 7 pre-existing rewriter test doubles widened for the new kwarg
- [x] **Part B — History rail restores transcripts, `thread_id` ships** (DoD: stable `thread_id` per conversation, rail click shows that transcript, v1 storage migrates, suites green)
  - `chat-store.ts` — `conversationId` on all three message types, stamped at creation; `messagesForConversation()` selector; persist `version: 2` + `migrate` stamping v1 messages with the first conversation id
  - `MessageStream.tsx` — renders the filtered conversation only
  - `types/chat.ts` — `ChatRequest.thread_id`; `Composer.tsx` (+ `FollowUpChips.tsx`, `ClarificationMessage.tsx`, required by the every-request DoD) sends `getState().activeConversationId`
  - `newChat` keeps past transcripts and pre-creates the next thread id (the plan's "keep null" shortcut would show old history in a fresh chat once transcripts persist, and breaks the plan's own B5 test — documented deviation, same id concept)
  - Tests: `chat-store.test.ts` (3), Composer `thread_id` (1, +2 call-shape updates), `chat-persist` stamp (1), rail-restore + your-documents badge in `EvidenceStack.test.tsx` (2)
- [x] **Part D — Forecasting v1 (`specs/11` §3.2)** (DoD: pure tested function, Actual/Projected graph + method/range narration, <4 → `None`, §3.2 checkbox checked, suites green)
  - `stats.py` — `is_forecast_query`, `infer_forecast_columns` (single-numeric-column only, else skip rather than guess), `compute_forecast` (linear regression on row index, `periods_ahead=1`)
  - `routes/chat.py` — forecast block alongside the what-if block (`computed["forecast"]`, fail-soft)
  - `pipeline/prompts.py` — FORECAST RULE next to WHAT-IF RULE; `pipeline/visuals.py` — `_forecast_graph_visual` (Actual + Projected with bridge point) + forecast-aware branch in `_visuals_from_rows`, no `GraphCard` changes
  - Tests: `TestComputeForecast` (5), `TestForecasting` chat e2e incl. dataset-pair assertion (1)
- [x] **Part C — Document QA (PDF/XLSX)** (DoD: XLSX like CSV; PDF → chunks; PDF-only answers from PDF; reserved sub-budget; `your-documents` label; kill-switch clean; gated integration test; suites green)
  - Config: `ENABLE_DOCUMENT_QA` + 7 budgeting/embedding fields (`app/config.py` `Field` entries); `requirements.txt` +`openpyxl`/`pypdf`/`pgvector`
  - XLSX first: `parser.parse_xlsx_bytes` → same clean + `upsert_user_table`; `ingest_file` gains `upload_id` (threaded from `routes/files.py`; CSV/XLSX ignore it)
  - PDF: `pdf_parser.py` (extract + overlap chunking), `embeddings.py` (HF Inference API, `EmbeddingError`, mean-pool fallback), `document_chunk.py` + registry + migration `c1code0000` (chains `b4code0000`, `CREATE EXTENSION vector`, ivfflat index), `vector_store.py` (`store_chunks`/`delete_file_chunks`/`search_chunks` user-scoped `<=>`/`user_has_document_chunks`/`retrieve_document_evidence` fail-soft, real `datetime` import)
  - Wiring: `parser.py` `.pdf` branch (`vector:<upload_id>` ref, honest scanned-PDF error); `chat.py` `_document_branch` in the evidence `gather`, reserved-sub-budget merge via `fit_pairs_to_budget` (signature verified: `(kept_texts, kept_sources, dropped)`), PDF-only SQL guard (`table_name=None` when no columns, introspection wrapped fail-soft for Postgres where PRAGMA would raise)
  - Labeling: `prompting.py` heading → `Retrieved Evidence (…tagged inline)` + per-line `, from your uploaded document` tag; `evidence.ts` third kind; `DataSources.tsx` badge + `FileText` icon
  - Tests: `test_pdf_parser.py` (8, fpdf2-generated fixtures via `importorskip`), `test_embeddings.py` (6, mocked httpx), `test_vector_store.py` (8, mocked session/embeds), `test_vector_store_integration.py` (gated on `TEST_POSTGRES_URL`), parser XLSX mirror (1), `test_files_api` XLSX-success/garbage updates, `TestDocumentEvidence` chat e2e (2)
- [x] **Docs**: `specs/04` (status/FR4/§4/edge-case/acceptance), `specs/11` (§3.2 status + checkbox), `Backend/docs/known-gaps.md` (XLSX/PDF closed, Pinecone superseded, forecast v1 noted), `Backend/CLAUDE.md` (orientation), `STATUS.md` (completed entry + all four living sections rewritten)

## Verification (this run, live)

- Backend: `.venv/bin/python -m pytest` from `Backend/` — **694 passed, 1 skipped** (658 baseline + 36 new; skip = gated pgvector integration); `alembic heads` = `c1code0000`
- Frontend: `npm test -- --run` from `Frontend/` — **24 files, 149 passed** (142 measured baseline + 7 new); `npm run build` ✅, `npm run lint` ✅

## Deviations from the plan (minimal, each required by a plan DoD/test)

- `build_sql_prompt` no-prior branch returns today's literal string (the plan's single-template sketch would add a blank line, breaking its own byte-identical guard).
- `newChat` preserves transcripts + pre-creates the thread id (plan text says both "must generate immediately" and "no change needed"; null + preserved transcripts shows old history in a fresh chat and fails the plan's B5 test).
- `get_table_columns` wrapped fail-soft for the PDF-only guard (on Postgres a missing table raises at PRAGMA instead of returning `[]`).
- `thread_id` also added to `FollowUpChips`/`ClarificationMessage` sends (DoD: *every* `/chat` request carries one; these are follow-up paths where continuity matters most).
- `evidence.ts` keeps the existing `subtitle` shape (no new subtitle invented beyond the plan's snippet).

## Explicitly out of scope (per plan §C9/§D4)

- OCR for scanned PDFs, structured table extraction (`pdfplumber` path), single-PDF delete/replace, cross-document re-ranking, seasonal/ARIMA forecasting, web/market-data forecasting, dashed-line chart styling.

---

# Whole-App Light/Dark Mode — Run Status (this run)

Persisted light/dark toggle (`system` default = follow OS) across auth screens + `/app`
workspace, via `<html data-theme>` + dual-theme tokens. Landing page keeps its art-directed
dark design by intent. Canonical living state is `STATUS.md` (uppercase).

## Completed

- [x] **Theme mechanism** (`index.css` dual-theme tokens incl. `--border-*`/`--fill-*`/`--overlay-backdrop`/`--on-accent`/`--chart-pie-1..6`; `theme-store.ts` persisted `system` default; `App.tsx` applicator; `index.html` pre-paint guard)
- [x] **Workspace + auth theming** (forced-dark blocks gated dark-only; ~35 literals → tokens; BoxLoader masks, route-guards, pie ramp follow theme; toggle in `ChatHeader` + `AuthLayout`)
- [x] **Tests**: 9 new (store 6, toggle 2, pie ramp 1); full suites green (see below)

## Verification (this run, live)

- Frontend: `npm test -- --run` from `Frontend/` — **139 passed** (up from 130); `npm run build` ✅, `npm run lint` ✅
- Backend: untouched this run — **658 passed** (unchanged)

---

`POST /chat/stream` already sent stage pings, but the answer itself arrived all at once at the
end. Now narration prose streams as SSE `text` deltas while generating; the final structured
result (visuals included) is unchanged. Canonical living state is `STATUS.md` (uppercase).

## Completed

- [x] **Backend streaming transport** (`groq_service.py`: `stream_response()` delta generator, key rotation, prose-JSON mode; `shared.call_llm_stream` shim forwarder; re-exported via `pipeline/__init__` + `langchain_pipeline` shim)
- [x] **Incremental answer extraction + narration wiring** (`pipeline/run.py`: `_extract_answer_prefix()`, `_narrate(..., on_token)` with identical parse/validate/repair + silent non-stream fallback, first-attempt-only streaming; `run_pipeline`/`_answer_request` thread `on_token`; `/chat/stream` emits `{"text"}` before `{"result"}`; unary `/chat` unchanged)
- [x] **Frontend live rendering** (`api/chat.ts` `onText`; store transient `streamingText`; Composer wiring + clear; MessageStream live prose block with cursor, reduced-motion respected; `message-stream.css`)
- [x] **Tests**: backend 9 new, frontend 6 new (+2 Composer assertions updated); full suites green (see below)

## Verification (this run, live)

- Backend: `python3 -m pytest` from `Backend/` — **658 passed** (up from 649)
- Frontend: `npm test -- --run` from `Frontend/` — **130 passed** (up from 124); `npm run build` ✅, `npm run lint` ✅

---

# Reliability Hardening — Run Status (this run)

Implementation of `implementation-plan-reliability-hardening.md`, Phases 0–3, followed literally in order.
Canonical living state is `STATUS.md` (uppercase) — this file is the per-run log only.

## Completed

- [x] **Phase 0 — Documentation hygiene (no behavior change)**
  - `STATUS.md`: `## Blocked / deferred` rewritten (stale B7 `source_scope`/F8 lines removed; kept spec-01, payments/F7, added PDF/XLSX QA + multi-LLM cascade); `## Tests / verification` + `## Last updated` regenerated from live runs; `## What's after` now notes the checkpoint bar was never defined and POST-CHECKPOINT hardening shipped anyway (explicit proceed decision)
  - Root `status.md` merged (only new item: `web_search.py` next-split-candidate note) then **deleted**; status-update rule added to `STATUS.md` top (`STATUS.md` is the single status file; neither `Backend/CLAUDE.md` nor `Frontend/CLAUDE.md` names a status file, so nothing to update there)
  - Green-fix required for DoD: `_DuckDuckGoParser._pending` shadowed `HTMLParser._pending` (Python 3.12 `super().close()` crash, 1 failed test) → renamed to `_pending_pair`, no behavior change

- [x] **Phase 1 — Groq structured outputs (`json_schema`/`strict: true`)**
  - `groq_service.py`: `_STRICT_SCHEMA_MODELS` allowlist, `generate_response(..., json_schema={"name","schema"})`, shared `_disable_json_transport` breaker extended to gate strict mode (no second mechanism), `_to_strict_schema()` helper (`additionalProperties: false` + full `required`, incl. nested `$defs`)
  - Call sites pass strict schemas built once: judge (`Decision`), narration (`PipelineOutput`), rewriter (new `RewriteOutput`); `plan_tools` intentionally unchanged
  - New `Backend/tests/test_groq_service.py` (5 tests: strict shape, `json_object` fallback, plain fallback, breaker disables strict, schema helper)

- [x] **Phase 2 — Validation-error repair retry**
  - `pipeline/run.py`: new `_attempt_validation_repair()` (original prompt + exact Pydantic error, same `json_schema` settings, same parse/validate path; `None` on any failure, never raises); `_narrate()` tries one repair on `ValidationError`, re-raises so `run_pipeline` still falls through to today's exact `_rescue_or_fallback`; happy-path call count unchanged
  - `TestValidationRepair` in `test_pipeline_contract.py` (3 tests: invalid→valid repair + error text in repair prompt; both-invalid → existing fallback; happy path stays at 2 calls)

- [x] **Phase 3 — SQL self-correction loop**
  - `chat.py::_execute_branch`: on 422, exactly one repair (real columns + real error fed back, same SQL settings), re-executed through unchanged `sanitize_sql` + `assert_user_scoped`; success updates `cleaned_sql` (traceability); second failure keeps today's sentinel → graceful-fallback path; `executor.py` untouched
  - Load-bearing fix found by the new tests: branch rollbacks expire ORM `User`, so post-rollback `user.id` crashed (`MissingGreenlet` 500) instead of the honest fallback → `user_id = user.id` captured upfront in `_answer_request`
  - `TestSqlSelfCorrection` in `test_chat_api.py` (3 tests: retry-and-succeed with 2 SQL calls; retry-also-fails → honest fallback with 2 calls; first-try success → 1 call)

## Verification (this run, live)

- Backend: `python3 -m pytest` from `Backend/` — **649 passed** (638 baseline + 11 new, no regressions)
- Frontend: `npm test -- --run` from `Frontend/` — **124 passed** (21 files, untouched)

## Files touched

- `Backend/app/services/web_search.py`, `Backend/app/services/llm/groq_service.py`
- `Backend/app/services/llm/pipeline/judge.py`, `Backend/app/services/llm/pipeline/run.py`, `Backend/app/services/llm/pipeline/__init__.py`
- `Backend/app/services/llm/query_rewriter.py`, `Backend/app/routes/chat.py`
- `Backend/tests/test_groq_service.py` (new), `Backend/tests/test_pipeline_contract.py`, `Backend/tests/test_chat_api.py`
- `STATUS.md` (per-phase updates, same change as each phase)

## Explicitly out of scope (per plan §4)

- Sandboxed Python/pandas code-execution path (separate initiative)

- Dedicated hallucination-detection / fact-checking LLM pass (not recommended; deterministic grounding + retries cover it)
