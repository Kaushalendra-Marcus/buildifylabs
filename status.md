# Answer Token Streaming — Run Status (this run)

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
