# Implementation Plan — Reliability Hardening (Self-Correction, Structured Output, Doc Hygiene)

**Written after (a) auditing the actual result of `implementation-plan-b7-evidence-hardening.md`
(all 6 phases + follow-on work genuinely shipped — see §0) and (b) researching 2026 industry
practice for low-hallucination LLM pipelines and how Julius.ai (the product you're benchmarking
against) is actually built. Same rules as the last plan: a coding agent should follow this
literally, one phase at a time, without re-deriving design decisions.**

---

## 0. Audit of the previous plan's execution — what actually shipped

Verified by reading the live source, not just the status logs:

- **B7 Evidence Hardening, all 6 phases** — done correctly. `context_budget.py` and
  `evidence_summarizer.py` exist and match spec; `web_search.py`'s final assembly now ranks +
  budgets the *whole* evidence pool (structured evidence deduped and placed first, then ranked
  snippets, one greedy fit against `MAX_EVIDENCE_CONTEXT_CHARS`) — actually a better integration
  than what the plan asked for, since it also protects the small structured-evidence block, not
  just the snippet pool. `chat.py` has the FR4 disagreement clarification and the
  `ENABLE_LIVE_WEB_SCOPE` kill switch, both wired through `effective_scope` as specified.
- **Beyond the plan, in the same work:** query framing raised to 1–4 queries (matching your
  original "3–4 queries" wording exactly), deep-read URLs 3→5, visual ceiling 6→7, and — most
  valuably — a **real production bug fix** (a phantom-entity extraction bug that was causing some
  comparison answers to ship zero visuals and an ASCII chart drawn in prose text; root-caused and
  fixed with a regression test).
- **A structural risk I flagged got addressed too, unprompted:** `langchain_pipeline.py` (6,523
  lines) and `comparison.py` (3,370 lines) were split into `app/services/llm/pipeline/` (12
  modules) and `app/services/data/comparison/` (7 modules), verified byte-identical import surface
  and zero behavior change (638 backend tests unchanged). This was explicitly **not** requested by
  the previous plan (it told the agent not to attempt this) — it was done anyway, and done
  correctly (AST-based verbatim move, test-patch compatibility preserved via a `shared.py`
  forwarder layer, cycle-checked imports). Worth knowing this happened, since every file path this
  new plan references below is a *post-split* path, not the old monolith.
- **Test counts across the whole run: 612 → 638 backend, 124 frontend, all green**, per the
  in-repo run logs — consistent with what the source actually shows.

**Two real gaps from that work, both doc-only, fix in Phase 0 below:**
1. `STATUS.md`'s "Completed tasks" section got new bullets prepended correctly, but the file's
   **living-state sections did not get regenerated**: `## Blocked / deferred` still lists "B7
   `source_scope` beyond `own_data` — needs Pinecone+Redis" and "B4 → frontend F8 live source-scope
   — gated on check B7", both now false; `## Tests / verification (this run)` still shows the old
   **149 backend / 65 frontend** counts, not the current 638/124; `## Last updated` still says
   2026-08-09. A future agent reading only those sections (a reasonable thing to do, since they're
   meant to be the current-state summary) will work from stale facts.
2. A **second, separate `status.md`** (lowercase, repo root) now exists alongside the canonical
   `STATUS.md`, written in a different, terser format by what looks like a different agent/run.
   Two files claiming to be "the status" is a real risk — the next agent might read only one and
   miss context that's only in the other.

---

## 1. What you're actually building vs. what Julius.ai does

You asked to benchmark against Julius.ai specifically, so here's the real architectural comparison
(researched, not assumed):

**Julius AI's core loop, in public materials:** natural-language prompt → the model writes
**executable Python** (pandas/numpy/etc., not SQL) → that code runs in a **sandboxed per-user
container/VM** → if the code errors, Julius runs a **self-correcting loop** (the error goes back to
the model, which fixes and re-runs) → the executed result (a real dataframe/number/plot) is what
gets narrated and charted. Julius also uses a **multi-model setup**, routing between GPT/Claude/
Gemini/its own models per task.

**Your product's current loop:** natural-language question → LLM generates **SQL** (safety-checked
via `sqlglot`, tenant-scoped) → SQL executes against the user's per-user table → `stats.py`
computes derived numbers **deterministically in pandas** → the LLM narrates those numbers (never
computes them itself) → a **code-level, non-LLM grounding pass** (`pipeline/grounding.py` —
`drop_ungrounded_visuals`, `_visual_numbers_grounded`, `apply_narration_contract`, etc.) checks
every number in every visual against the real evidence before it ships, dropping anything
ungrounded.

**These are the same idea at the core** — never let the LLM invent or compute a number, always let
code do it and have the LLM only narrate — which is good, because that's exactly the principle
2026 research keeps landing on (see §2). The real difference is **expressiveness and resilience**:

- SQL can only answer what's SQL-expressible. Python/pandas can also do correlation, cohort
  analysis, simple forecasting, joins across reshaped data, anything `numpy`/`scipy` can do. This
  is a real ceiling on how "smart" your analysis can get without a code-execution path — noted
  explicitly in §7 as a **strategic, not urgent**, consideration.
- Julius **retries itself** when generated code fails (self-correcting loop). Your SQL path
  currently does **not** — a hallucinated column name today returns a 422 asking the user to
  rephrase, rather than the system silently fixing it. This is the single highest-value gap closed
  in this plan (Phase 3) — no new architecture needed, same SQL path, just a bounded retry.

---

## 2. What 2026 research actually says (and how your system already stacks up)

Summarized from current-year sources (hallucination-mitigation surveys, RAG faithfulness
benchmarks, structured-output engineering guides, LLM-as-judge evaluations):

- **Grounding + a strict citation contract is still the single highest-leverage lever**, and
  contextual compression (rank, then fit to a budget, rather than blind truncation) measurably
  improves signal-to-noise. **You already do both** — numbered `[n]` citations tied 1:1 to sources,
  and the ranking/budgeting work from the previous plan.
- **A widely-replicated 2026 clinical-AI finding worth knowing: naive RAG can *increase*
  hallucination; structured representation with explicit provenance is what actually moves the
  ceiling on factual reliability.** This directly validates your architecture's bias toward
  deterministic computation (SQL/pandas stats, structured Yahoo/FRED/Wikipedia adapters, the
  `grounding.py` provenance system) over "just retrieve more text and hope the model reads it
  right." Don't let a Julius-style code-execution path (§7) become an excuse to retrieve-and-narrate
  more loosely than you do today — keep the "code computes, LLM only narrates" rule even there.
- **Confidence must be evidence-capped, not self-reported** (LLMs are consistently overconfident
  when asked to grade their own certainty). **You already do this** — strong/thin/none evidence
  caps confidence at 0.90/0.65/0.35.
- **LLM-as-judge is genuinely useful in production (your sufficiency judge is exactly this
  pattern) but has real, measured failure modes** — position bias, verbosity bias, and a
  "reliability without validity" gap (a judge can be internally consistent and still wrong). The
  practical implication: **don't reach for "add another LLM call to fact-check the first LLM
  call"** as the next reliability lever — you already have the higher-leverage, lower-bias
  alternative available (deterministic retry loops, schema-constrained decoding), which is what
  this plan spends its budget on instead.
- **"Retry-with-clarification" (feed the exact validation/execution error back to the model for one
  targeted fix) is now the standard structured-output pattern**, and **schema-constrained decoding
  (not just "JSON mode") is the current recommended baseline** wherever the provider supports it.
  **Groq does support this** — `response_format: {"type": "json_schema", "json_schema": {...},
  "strict": true}` with guaranteed 100% schema adherence — **for `openai/gpt-oss-20b` and
  `openai/gpt-oss-120b` specifically**. Your `.env` already sets `GROQ_MODEL=openai/gpt-oss-120b`
  (correctly migrated off the now-decommissioned `llama-3.3-70b-versatile`, per your own `.env`
  comment — good, that was worth double-checking given the decommission date already passed).
  **You are one config-and-code change away from a real, structural reliability upgrade that costs
  nothing extra to call.** This is Phase 1 below, and it's the highest-value item in this plan.
- Text-to-SQL accuracy in current benchmarks is reported in a wide **25–75%** band depending on
  query complexity — reinforcing that a retry/self-correction loop (Phase 3) isn't a nice-to-have,
  it's addressing a documented, expected failure rate, not a hypothetical one.

**Bottom line for you:** your grounding/citation/confidence/ranking layers are already at or above
2026 best practice. The gap is specifically in **what happens after something goes wrong** — SQL
execution errors and JSON validation errors both currently go straight to a degraded response
instead of one bounded, targeted self-correction attempt. That's what Phases 1–3 close.

---

## Non-negotiable constraints (same as the previous plan — re-read before each phase)

- Generic, not hardcoded. No new company/industry/category lists.
- The LLM never computes numbers; code does. This plan does not touch that rule — it only adds
  *retry* around existing code-computes/LLM-narrates call sites.
- Fail soft. Every new retry path must have a bounded attempt count and a safe, honest fallback
  (today's exact fallback behavior) if the retry also fails — never a worse failure mode than today.
- One phase at a time; full test suite green before the next phase; test counts only go up.
- Update `STATUS.md` in the same change as every phase, and follow the corrected process from
  Phase 0 (regenerate the living-state sections, don't just prepend to "Completed tasks").
- All file paths below are **post-split** paths (`app/services/llm/pipeline/...`,
  `app/services/data/comparison/...`) — confirm the exact file before editing; the split may have
  put a function you're looking for in a sibling module to the one named here if this plan is run
  after further refactors.

---

## Phase 0 — Documentation hygiene (no behavior change)

**Goal:** `STATUS.md` says one true, current thing; there is exactly one status file.

**Tasks:**
1. In `STATUS.md`, rewrite (not append to) these sections so they reflect the current repo state:
   - `## Blocked / deferred` — remove the two now-false B7 lines; keep genuinely still-blocked
     items (payments/F7 upgrade UI, spec-01 completeness, PDF/XLSX document QA, multi-LLM
     cascade).
   - `## Tests / verification (this run)` — replace the stale 149/65 counts with the actual
     current counts (run `pytest` from `Backend/` and `npm test` from `Frontend/` to get real
     numbers at the time you make this edit — don't copy a number from any log file, confirm it
     live).
   - `## Last updated` — real current date and a one-line summary of the most recent work.
   - `## What's after` — either resolve the "no POST-CHECKPOINT phase starts before real-user
     evidence" line against what's actually happened (B7/B5-adjacent work shipped without that bar
     being defined), or explicitly note the decision to proceed anyway. Don't leave the file
     asserting a policy that visibly wasn't followed — that's confusing for the next reader more
     than the policy question itself matters.
2. Read the root-level `status.md` (lowercase). Merge anything in it not already captured in
   `STATUS.md`'s "Completed tasks" into that section (it largely is already, but confirm), then
   **delete `status.md`**. Going forward, `STATUS.md` (uppercase) is the single status file —
   confirm this is also stated in `Backend/CLAUDE.md`/`Frontend/CLAUDE.md` if either references a
   status file by name.
3. Add one sentence to whichever doc governs how status updates are made (or `STATUS.md`'s own
   top, if nothing else governs it): "Every status update must rewrite `## Blocked/deferred`, `##
   Tests / verification`, `## Last updated`, and `## What's after` to match the current repo state
   — these are a living snapshot, not a log; `## Completed tasks` is the only append-only section."
   This is the actual fix — without it, the same staleness recurs next run.

**Definition of Done:** one status file; every "current state" section in it is true right now,
verified by actually running the test suites, not copied from a prior log.

---

## Phase 1 — Groq structured outputs: JSON-schema strict mode (highest value, do this first)

**Goal:** replace `response_format: {"type": "json_object"}` with Groq's schema-constrained
decoding (`{"type": "json_schema", "json_schema": {...}, "strict": true}`) for the pipeline's
structured calls, eliminating an entire class of shape/type validation failures at the API level
instead of catching them after the fact.

**Why this is safe to do now:** your `GROQ_MODEL` (`openai/gpt-oss-120b`) and `GROQ_FAST_MODEL`
(unset → falls back to `GROQ_MODEL`, per `Settings.groq_fast_model`) are both
`openai/gpt-oss-120b` — one of the exact two models Groq's structured-outputs docs list as
supporting `strict: true`. If either config value is ever changed to a model outside
`{openai/gpt-oss-20b, openai/gpt-oss-120b}`, this feature silently stops being available for that
call — Phase 1 must include a runtime capability check, not an assumption baked in once.

**File to touch:** `Backend/app/services/llm/groq_service.py`.

1. Add a small allowlist near the top:
   ```python
   # Models confirmed to support Groq's strict json_schema structured outputs
   # (constrained decoding — 100% schema adherence). Confirm against
   # https://console.groq.com/docs/structured-outputs before adding a model;
   # an unlisted model silently falls back to json_object below, never errors.
   _STRICT_SCHEMA_MODELS = {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
   ```
2. Extend `generate_response(...)` with an optional parameter:
   ```python
   async def generate_response(
       prompt: str,
       system_prompt: str = "You are a helpful AI assistant.",
       model: Optional[str] = None,
       temperature: float = 0.3,
       max_tokens: int = 512,
       json_mode: bool = False,
       json_schema: Optional[dict] = None,   # NEW: {"name": ..., "schema": {...}}
   ) -> dict:
   ```
   Where the request's `response_format` is built:
   ```python
   use_strict_schema = (
       bool(json_schema)
       and selected_model in _STRICT_SCHEMA_MODELS
       and not _json_transport_disabled(selected_model)
   )
   use_json_transport = bool(json_mode) and not _json_transport_disabled(selected_model)
   extra: dict = {}
   if use_strict_schema:
       extra["response_format"] = {
           "type": "json_schema",
           "json_schema": {
               "name": json_schema["name"],
               "strict": True,
               "schema": json_schema["schema"],
           },
       }
   elif use_json_transport:
       extra["response_format"] = {"type": "json_object"}
   ```
   On a 400 from this transport, reuse the **existing** `_disable_json_transport(model)` circuit
   breaker (it already exists in this file) so a model/schema combination that fails once falls
   back to `json_object` for a cooldown period, exactly like the current json_object-vs-plain-text
   circuit breaker already does. Do not write a second, parallel circuit-breaker mechanism — extend
   the one that's already there to also gate `use_strict_schema`, not just `use_json_transport`.

**Call sites to update** — three structured JSON calls in `app/services/llm/pipeline/`
(post-split locations; confirm exact function names by reading `pipeline/judge.py`, `pipeline/run.py`
(narration), and `query_rewriter.py` before editing — do not guess signatures):

1. **Sufficiency judge** (`pipeline/judge.py`) — its `Decision` Pydantic model (already defined,
   per `specs/06` §3) has a fixed, known shape. Build its JSON schema once with
   `Decision.model_json_schema()` and pass `json_schema={"name": "decision", "schema": <that
   dict>}` instead of `json_mode=True`.
2. **Narration / `PipelineOutput`** (`pipeline/run.py` or `pipeline/prompting.py`, wherever the
   narration call is made) — same approach with `PipelineOutput.model_json_schema()`. **Caveat to
   check before wiring this one:** Groq's `strict: true` mode requires every object to set
   `additionalProperties: false` and list every property as `required` (per Groq's docs). Pydantic
   v2's `model_json_schema()` output may need `Optional[...]` fields handled carefully (a `strict`
   schema wants them present-but-nullable, not absent) — read Groq's structured-outputs docs
   examples directly (`https://console.groq.com/docs/structured-outputs`) before assuming
   `model_json_schema()`'s raw output is accepted as-is; you may need a small schema
   post-processing helper (`_to_strict_schema(pydantic_model) -> dict`) that walks the generated
   schema and forces `additionalProperties: false` + full `required` lists. Write this helper once,
   reuse it for all three call sites below.
3. **Query rewriter** (`query_rewriter.py`) — its rewritten-queries output shape, same treatment.

**Tests to add/update:**
- `Backend/tests/test_groq_service.py` (find or create): mock the Groq client's
  `chat.completions.create`, assert that when `json_schema` is passed and the model is in
  `_STRICT_SCHEMA_MODELS`, the request's `response_format` is the `json_schema`/`strict: true`
  shape, not `json_object`; assert a model *not* in the allowlist with `json_schema` passed still
  falls back to `json_object` (or plain, if `json_mode` wasn't also set) — never raises, never
  silently drops the schema requirement without falling back to something.
- Assert the existing `_disable_json_transport` circuit breaker, once tripped for a model, also
  disables `use_strict_schema` for that model during its cooldown window (a new assertion on an
  existing test, not a new test file, if a `_no_json_transport` test already exists).
- Existing `test_pipeline_contract.py` schema-validation tests should now see **fewer**
  `ValidationError` fallback paths exercised in practice, but keep those tests as-is — they're
  still the correct regression guard for the (rarer, now) case where schema mode isn't available.

**Definition of Done:** the three call sites request `json_schema`/`strict: true` when the active
model supports it; a model swap to an unsupported model degrades cleanly to today's `json_object`
behavior (proven by a test, not just believed); full suite green.

---

## Phase 2 — Validation-error repair retry (JSON shape failures that Phase 1 doesn't already prevent)

**Goal:** for the (now rarer, since Phase 1 lands first) case where narration output still fails
Pydantic validation — including for any Groq model that ends up outside the strict-schema allowlist
— attempt **one** targeted repair before falling to `_rescue_or_fallback`, per the 2026
"retry-with-clarification" pattern: send the exact validation error back, not a blind
re-ask.

**File to touch:** wherever the narration call + `except ValidationError` block live in
`app/services/llm/pipeline/run.py` (confirm exact location — the previous audit found it around
the `run_pipeline` exception handling, search for `except ValidationError as ve:`).

**Design:**
```python
except ValidationError as ve:
    logger.error(f"Schema validation failed: {ve}")
    repaired = await _attempt_validation_repair(
        original_prompt=narration_prompt,
        system_prompt=SYSTEM_PROMPT,
        raw_output=raw_output,        # the exact text that failed to validate
        validation_error=str(ve),     # Pydantic's error, has field paths + reasons
        model=settings.groq_fast_model,
    )
    if repaired is not None:
        output = repaired
        # continue into the same post-processing (guarantee, provenance, trace log)
        # the happy path already runs -- do not duplicate that logic, restructure
        # so both paths converge before ensure_visuals/grounding runs.
    else:
        return await _rescue_or_fallback(
            reason="Sorry, I could not process your request properly. Please try again."
        )
```

`_attempt_validation_repair` (new small function, same file or a new `pipeline/repair.py` if that
fits the existing module boundaries better — check `pipeline/__init__.py`'s exports first):
- Builds one follow-up prompt: the original narration prompt + `"\n\nYour previous response failed
  validation: {validation_error}. Return corrected JSON only, fixing exactly what's described
  above — do not change anything that wasn't flagged."`
- Calls `generate_response` **once** (bounded — no loop) with the same `json_schema`/`json_mode`
  settings as the original call.
- Parses + validates the result the same way the happy path does (reuse
  `normalize_pipeline_payload(extract_json(...))` — do not duplicate that parsing logic).
- Returns the validated `PipelineOutput` on success, `None` on any further failure (caught, logged,
  never raised) — the caller falls through to today's exact fallback either way.

**Tests to add** — `Backend/tests/test_pipeline_contract.py`:
- Mock `generate_response` to return invalid JSON shape on the first call (e.g. `confidence: 1.4`,
  out of the `Field(ge=0.0, le=1.0)` range) and a valid, corrected payload on the second call —
  assert the final `PipelineOutput` is the corrected one, and that the second call's prompt
  contained the specific validation error text (not a generic "try again").
- Mock `generate_response` to fail validation on **both** calls — assert it falls through to the
  existing `_rescue_or_fallback` path unchanged (this is the critical regression test: the repair
  attempt must never replace the safety net, only sit in front of it).
- Confirm this doesn't double the latency budget silently — this is one extra call only on the
  failure path, never on the happy path; add a comment/assertion that the happy path's call count
  is unchanged.

**Definition of Done:** both new tests pass; existing fallback tests (bad JSON / empty visuals /
exception → fallback with `reason`) still pass unchanged — this phase narrows *when* the fallback
triggers, it does not change what the fallback looks like.

---

## Phase 3 — SQL self-correction loop (the single highest-value behavior gap vs. Julius.ai)

**Goal:** when generated SQL fails to execute against the user's real table (hallucinated
column/table name — the exact failure mode already named in `executor.py`'s own docstring), retry
**once** with the real error and real schema fed back to the model, instead of surfacing a 422
asking the user to rephrase. This is the direct, minimal-risk analog of Julius's self-correcting
code-execution loop, built entirely inside your existing SQL path — no sandboxing, no new
architecture.

**Files to touch:** `Backend/app/routes/chat.py` (wherever `execute_sql` is currently called —
likely inside `_answer_request`, right after the initial SQL generation) and
`Backend/app/services/data/executor.py` (only if `execute_sql`'s signature needs a small addition,
not its safety logic).

**Design — do not change `executor.py`'s safety guarantees (`sanitize_sql`, `assert_user_scoped`)
in any way, only add a retry around the calling code:**

```python
try:
    rows = await execute_sql(sql_query, db, user_table)
except HTTPException as exc:
    if exc.status_code == 422 and not already_retried:
        real_columns = await get_table_columns(db, user_table)
        repair_prompt = build_sql_prompt(
            user_query=request.query,
            schema=build_data_schema(user_table, real_columns),
        ) + (
            f"\n\nYour previous query failed with this database error:\n{exc.detail}\n"
            "Return a corrected query using only the exact column names listed above."
        )
        repaired_raw = await generate_response(repair_prompt, system_prompt=SQL_SYSTEM_PROMPT)
        repaired_sql = clean_sql_response(repaired_raw["content"])
        try:
            rows = await execute_sql(repaired_sql, db, user_table)
            sql_query = repaired_sql  # so data_preview/sql_query in the response reflect what actually ran
        except HTTPException:
            raise  # second failure surfaces the honest 422, exactly like today
    else:
        raise
```

Bound this to **exactly one** retry (`already_retried` flag) — never a loop. The repaired query
still goes through `sanitize_sql` + `assert_user_scoped` inside `execute_sql` unchanged — the retry
gets **zero** additional trust, it's just a second attempt at generating a query, validated exactly
as strictly as the first one. Log both the original error and whether the repair succeeded (a
`research_notes`-style disclosure is not appropriate here since this isn't user-facing evidence
provenance — a plain `logger.info` is enough, this is an internal resilience mechanism, not
something the answer needs to disclose to the user, unlike the evidence-trimming notes from the
previous plan).

**Tests to add** — `Backend/tests/test_chat_api.py`:
- `test_sql_error_triggers_one_retry_and_succeeds`: monkeypatch `generate_response` to return a SQL
  referencing a wrong column name on the first call and a correct query on the second — assert the
  final `/chat` response is a normal successful `PipelineOutput` (not a 422), and that
  `generate_response` (or whatever the SQL-generation entry point is) was called exactly twice for
  this request.
- `test_sql_error_retry_also_fails_returns_honest_422`: monkeypatch both calls to produce
  SQL that fails execution — assert the response is still the existing 422 with the existing
  message, proving the retry doesn't mask a genuinely bad question with a silent wrong answer or an
  infinite loop.
- `test_sql_success_on_first_try_never_triggers_retry`: the existing happy-path tests
  (`test_chat_api.py`'s current happy-loop test) should already cover this — confirm they still
  pass with call-count assertions added if not already present, to prove this phase adds zero
  overhead to the common case.

**Definition of Done:** all three tests pass; the two new tests are the actual proof this behaves
like Julius's self-correcting loop (retry-with-real-error, bounded, never lowers the safety bar);
existing SQL-safety tests (`assert_user_scoped`, `sanitize_sql` rejection cases) pass completely
unchanged, since this phase never touches what "safe" means, only how many chances the model gets
to produce something safe *and* correct.

---

## 4. Explicitly out of scope for this plan (strategic, not urgent — separate decision)

**A sandboxed Python/pandas code-execution path**, as a complement or eventual alternative to
NL→SQL for questions that aren't SQL-expressible (correlation, cohort/segmentation analysis,
simple forecasting beyond what `specs/11` already computes deterministically, arbitrary
reshaping). This is the actual architectural difference between you and Julius.ai (§1) and the
main lever left if you want to close the *capability* gap, not just the *reliability* gap this plan
closes. It's out of scope here because it's a genuinely different initiative — per-user sandboxed
execution (security boundary, resource limits, cost per session), not a hardening pass on the
existing pipeline — comparable in size to the PDF/document-RAG item flagged as out-of-scope in the
previous plan. If you want this, ask for a dedicated plan; don't fold it into a reliability-hardening
pass, for the same reason stated in that earlier plan: it would make every phase above riskier for
no reason.

**A dedicated hallucination-detection/fact-checking LLM pass** (a second model scoring the first
model's claims). Per §2's research, this has real, measured limitations (bias, reliability-without-
validity) and is lower-leverage than what Phases 1–3 already do. Not recommended as a near-term
addition; the deterministic grounding you already have (`pipeline/grounding.py`) plus Phases 1–3's
retry loops address more of the actual risk surface for less engineering cost and less introduced
bias than an LLM-judge-on-top-of-an-LLM-judge pattern would.

---

## 5. Suggested execution order

Phase 0 (hygiene, fast, do first so the next status update is accurate) → **Phase 1** (highest
value, do this before 2 and 3 — it reduces how often Phase 2's repair path even triggers) → Phase 2
→ Phase 3. Phase 3 is independent of 1/2 and could be done in parallel by a second agent session if
you want to parallelize, but do not skip Phase 1 in favor of Phase 3 — Phase 1 is free (no latency
cost on the happy path) and structurally prevents a whole class of failure, where Phase 3 only
recovers from one after it happens.
