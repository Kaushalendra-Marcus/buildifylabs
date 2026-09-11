# BuildifyLabs — STATUS Docs

Tracks one-task-at-a-time progress against
[`implementation-plan-master.md`](./implementation-plan-master.md). Source of truth for product
requirements is `specs/`; re-read only the relevant plan/phase before each run.
`STATUS.md` (uppercase, repo root) is the single status file.

Every status update must rewrite `## Blocked / deferred`, `## Tests / verification (this run)`, `## Last updated`, and `## What's after` to match the current repo state — these are a living snapshot, not a log; `## Completed tasks` is the only append-only section.

Legend: ✅ completed · ⚠️ partial · ⛔ blocked · ⏸ deferred/paused

## Current task

**🚩 CHECKPOINT — put the core loop in front of real users before any POST-CHECKPOINT phase.**
`[IMMEDIATE]` (`specs/00` §7, master plan Part F/B boundary). All immediate/MVP frontend phases
(F0–F6) and backend phases (B0–B4) are complete. **Do not start any POST-CHECKPOINT phase
(B5+ / F7+) until the "worth continuing" bar is defined** (e.g. % of first-time users asking a 2nd
question in-session) and the core loop has real-user evidence.

## Completed tasks

- **Whole-app light/dark mode with user toggle** — done, test-verified (frontend **139 tests**, all green, up from 130; `npm run build` + `npm run lint` clean; backend untouched — **658 tests**):
  - Mechanism: `<html data-theme>` (`light`/`dark` explicit, absent = follow OS) driven by a persisted `theme-store` (`system` default) + `index.html` pre-paint guard; root tokens carry both themes (`color-scheme` included); new interaction tokens (`--border-*`, `--fill-*`, `--overlay-backdrop`, `--on-accent`, `--chart-pie-1..6`).
  - Workspace + auth surfaces: forced-dark intelligence blocks now apply dark-only (light inherits root light tokens, blue accent); ~35 white-alpha literals → theme tokens; on-accent text, BoxLoader masks, route-guards loading screen, and the pie ramp (token-read with fallback) all follow the theme; charts re-render on switch.
  - Toggle (`ThemeToggle`, Sun/Moon) in the app header and on auth screens; scope note: the marketing landing page keeps its art-directed dark design by intent.
  - Tests: 9 new (store 6, toggle 2, pie token ramp 1).
  - Files: `Frontend/src/index.css`, `Frontend/index.html`, `Frontend/src/App.tsx`, `Frontend/src/lib/theme-store.ts` (+ test), `Frontend/src/components/ThemeToggle.tsx` (+ test/css), `Frontend/src/features/chat/ChatHeader.tsx`, `Frontend/src/features/auth/AuthLayout.tsx` + `auth.css` + `route-guards.css`, `Frontend/src/features/chat/chat-workspace.css` + `composer.css` + `message-stream.css`, `Frontend/src/components/BoxLoader.tsx`/`.css`, `Frontend/src/components/visuals/GraphCard.tsx`, `Frontend/src/components/visuals/VisualCard.test.tsx`.

- **Answer token streaming — narration prose streams live, visuals land with the result** — done, test-verified (backend **658 tests**, all green, up from 649; frontend **130 tests**, all green, up from 124; `npm run build` + `npm run lint` clean):
  - Backend: new `groq_service.stream_response()` delta generator (key rotation, no `response_format` prose-JSON mode so it works on any model; raises → callers fall back); `shared.call_llm_stream` shim forwarder + `stream_response` re-exported through `pipeline/__init__` and the `langchain_pipeline` shim; `_extract_answer_prefix()` incremental JSON-string decoder; `_narrate(..., on_token)` streams first-attempt prose then runs the exact same parse + validate + repair path (stream failure → silent non-streamed fallback; backstop re-narrations stay non-streamed); `run_pipeline`/`_answer_request` thread `on_token`; `/chat/stream` emits `{"text": delta}` events before the final `{"result"}`; unary `/chat` unchanged.
  - Frontend: `sendQuery(body, onStage?, onText?)` handles `text` events; store gains transient `streamingText` (+append/clear, never persisted, cleared on submit/result/new-chat); Composer wires `onText`; MessageStream renders the live prose block (`AnswerProse` + blinking cursor, citations plain mid-stream, `prefers-reduced-motion` respected).
  - Tests: backend 9 (stream transport 4, extraction + narration streaming/fallback 4, SSE text-events e2e 1); frontend 6 (api text forwarding 2, store accumulate/clear/no-persist 2, streaming render + cleared-state 2; 2 Composer call-shape assertions updated for the new arg).
  - Files: `Backend/app/services/llm/groq_service.py`, `.../pipeline/shared.py`, `.../pipeline/__init__.py`, `.../langchain_pipeline.py`, `.../pipeline/run.py`, `Backend/app/routes/chat.py`, `Backend/tests/test_groq_service.py`, `Backend/tests/test_pipeline_contract.py`, `Backend/tests/test_chat_api.py`, `Frontend/src/api/chat.ts`, `Frontend/src/api/chat.test.ts`, `Frontend/src/features/chat/chat-store.ts`, `Frontend/src/features/chat/chat-persist.test.ts`, `Frontend/src/features/chat/Composer.tsx`, `Frontend/src/features/chat/Composer.test.tsx`, `Frontend/src/features/chat/MessageStream.tsx`, `Frontend/src/features/chat/MessageStream.test.tsx`, `Frontend/src/features/chat/message-stream.css`.

- **Reliability hardening Phase 3 — SQL self-correction loop** — done, test-verified (backend **649 tests**, all green, up from 646; frontend untouched — **124 tests**):
  - `app/routes/chat.py::_execute_branch`: on `HTTPException` 422 (hallucinated column/table), exactly one repair — real columns via `get_table_columns` + real error fed back through `build_sql_prompt`/`generate_response`/`clean_sql_response`, re-executed through unchanged `sanitize_sql` + `assert_user_scoped` (zero additional trust); success updates `cleaned_sql` so `sql_query`/`data_preview` reflect what ran; second failure keeps today's exact sentinel → graceful-fallback path. Internal `logger.info` only, no user-facing disclosure. `executor.py` safety logic untouched.
  - Load-bearing fix found by the new tests: evidence-branch rollbacks expire the ORM `User`, so post-rollback `user.id` lazy-loads crashed with `MissingGreenlet` (500) instead of the honest fallback — `_answer_request` now captures `user_id = user.id` upfront and uses it throughout (identical on the happy path).
  - Tests: `TestSqlSelfCorrection` in `tests/test_chat_api.py` (3 tests: bad-column → one retry → successful `PipelineOutput` with corrected `sql_query`, 2 SQL calls; both-calls-bad → today's graceful fallback, 2 calls, no loop/masking; first-try success → 1 call, zero happy-path overhead). Existing `assert_user_scoped`/`sanitize_sql` suites unchanged.
  - Files: `Backend/app/routes/chat.py`, `Backend/tests/test_chat_api.py`.

- **Reliability hardening Phase 2 — validation-error repair retry** — done, test-verified (backend **646 tests**, all green, up from 643; frontend untouched — **124 tests**):
  - `pipeline/run.py`: new `_attempt_validation_repair()` (one bounded follow-up: original prompt + exact Pydantic error, same `json_schema` settings, same parse/validate path; returns validated `PipelineOutput` or `None`, never raises); `_narrate()` catches `ValidationError`, tries one repair, re-raises on further failure so `run_pipeline`'s `except ValidationError` still falls through to today's exact `_rescue_or_fallback`. Happy-path call count unchanged (repair runs only on validation failure).
  - Tests: `TestValidationRepair` in `tests/test_pipeline_contract.py` (3 tests: invalid→valid repairs + repair prompt carries the specific error text; both-calls-invalid falls through to the existing fallback; happy path stays at 2 calls).
  - Files: `Backend/app/services/llm/pipeline/run.py`, `Backend/app/services/llm/pipeline/__init__.py`, `Backend/tests/test_pipeline_contract.py`.

- **Reliability hardening Phase 1 — Groq structured outputs (strict json_schema mode)** — done, test-verified (backend **643 tests**, all green, up from 638; frontend `npm test` all green — **124 tests**, untouched):
  - `groq_service.py`: `_STRICT_SCHEMA_MODELS` allowlist (`openai/gpt-oss-20b`, `openai/gpt-oss-120b`); `generate_response(..., json_schema={"name", "schema"})` requests `json_schema`/`strict: true` on supported models, falls back to `json_object` (or plain when `json_mode` unset) otherwise; existing `_disable_json_transport` breaker extended to gate strict mode too (no second mechanism); `_to_strict_schema()` post-processes Pydantic v2 schemas (`additionalProperties: false` + full `required`, incl. nested `$defs`).
  - Call sites now pass strict schemas built once: judge (`Decision`), narration (`PipelineOutput`), rewriter (`RewriteOutput`); `plan_tools` intentionally unchanged (`json_mode`).
  - Tests: new `tests/test_groq_service.py` (5 tests: strict shape on supported model, `json_object` fallback + plain fallback on unsupported models, breaker disables strict during cooldown, schema post-processing helper).
  - Files: `Backend/app/services/llm/groq_service.py`, `Backend/app/services/llm/pipeline/judge.py`, `Backend/app/services/llm/pipeline/run.py`, `Backend/app/services/llm/query_rewriter.py`, `Backend/tests/test_groq_service.py` (new).

- **Codebase scalability — split the two giant single-file modules into focused packages** — done, test-verified (backend **638 tests**, all green, unchanged; frontend `npm test` all green — **124 tests**, untouched; zero behavior change, pure move):
  - `langchain_pipeline.py` (6,523 lines) → `app/services/llm/pipeline/` (12 modules, biggest `run.py` 1,300): `models` (contracts), `prompts` (prompts/intent regexes), `deps` (guarded comparison imports, verbatim), `judge` (sufficiency judge + tool routing), `prompting` (prompt assembly/citations), `figures` (figure extraction/binding), `visuals` (component builders), `grounding` (number grounding/provenance), `history` (comparison gate + what-if), `guarantee` (`ensure_visuals`), `run` (`run_pipeline`), `shared` (shim indirection, see below). Old path kept as a 15-line shim (`langchain_pipeline.py`) — every existing import works.
  - `comparison.py` (3,370 lines) → `app/services/data/comparison/` (7 modules, biggest `evidence.py` 1,695): `symbols`, `entities`, `timeframe`, `currency`, `plans`, `figures`, `evidence`. Old dotted path resolves to the package with an identical surface (regular packages shadow same-named files; unreachable shim file removed).
  - Test-patch compatibility preserved without touching any test: 4 names patched by tests (`generate_response` ×20, `_historical_comparison_gate` ×2, `_visual_numbers_grounded` ×1, `check_research_completeness` ×1) resolve through `pipeline/shared.py` forwarders at call time; production objects unchanged. Verified: module `dir()` surfaces byte-identical to pre-split (snapshot-compared), function identity holds (`shim.X is pkg.mod.X`).
  - Method: AST-based verbatim move (order/duplicates/comments preserved), auto-wired sibling imports with cycle detection (one real cycle found + fixed: typing-cue regexes live with `classify_entity_type` in `symbols.py`), per-module `__all__` so private helpers stay importable.
  - Files: deleted `Backend/app/services/data/comparison.py`, rewrote `Backend/app/services/llm/langchain_pipeline.py` as shim, new `pipeline/` (12 files) + `comparison/` (7 files + `__init__`). No logic edits, no test edits, no frontend changes.
  - Follow-on note (merged from `status.md`, now deleted): fix bugs in the small module that owns them; keep the `shared.py` forwarder pattern for any name tests patch on the shim; `web_search.py` (2,512 lines) is the next split candidate if it keeps growing — same recipe applies.

- **Visual-empty fix — phantom-entity gate no longer swallows snippet visuals + no-text-charts rule** — done, test-verified (backend **638 tests**, all green, up from 634; frontend `npm test` all green — **124 tests**, untouched):
  - Root cause (production screenshot: pricing comparison, correct prose, zero component visuals, ASCII bars in text): `decompose_comparison_query` ghosted "Cost Of Top"/"Output Prize Both" as entities → comparison gate applied → validated-history branch found no history and returned a naked answer before figure synthesis ever ran; plus no prompt rule forbade ASCII charts.
  - `comparison.py` `_INDICATOR_TOKENS` += top/inputs/outputs/prizes/both/either/neither (generic scaffolding, exact-token match — pinned ghost cases unaffected): query now yields zero entities, gate doesn't fire, 8 snippet money figures attribute via fallback → comparison + bar + figures/products tables + timeline (0 → 5 visuals, verified).
  - `langchain_pipeline.py::ensure_visuals`: validated-history branch returns early only when it actually synthesized something; otherwise falls through to row/series/figure synthesis (same final provenance filter, BLOCKED path untouched — verified real-entity-no-evidence comparisons still ship no graph/comparison).
  - `SYSTEM_PROMPT`: NO TEXT CHARTS rule (numbers belong in the 7 components, prose carries the takeaway).
  - Tests: ghost case F (no phantom entities, gate off, fallback clean), `TestPricingComparisonVisuals` (≥4 visuals incl. comparison/graph/Figures cited/Products compared), trust-boundary pin (no fabricated charts), prompt ASCII-ban marker.
  - Files touched: `Backend/app/services/data/comparison.py`, `Backend/app/services/llm/langchain_pipeline.py`, `Backend/tests/test_ghost_entities_regression.py`, `Backend/tests/test_pipeline_contract.py`.

- **Web-evidence flow completion — 4-query framing, whole-pool budgeting, deeper page reads, 7-visual ceiling** — done, test-verified (backend **634 tests**, all green, up from 632; frontend `npm test` all green — **124 tests**, untouched):
  - Query framing (`query_rewriter.py` + `web_search.py`): `MAX_REWRITE_QUERIES` 3 → 4 (prompt text updated to 1–4) and `MAX_SEARCH_QUERIES` 3 → 4 so the 4th framed query is actually searched, not truncated.
  - Whole-pool budgeting (`web_search.py` final assembly): structured evidence is deduped as (text, source) pairs (also fixing pair alignment), placed first, then ranked snippets — one greedy fit guarantees the TOTAL stays within `MAX_EVIDENCE_CONTEXT_CHARS`; trim disclosure via `research_notes` unchanged.
  - Deep reads (`config.py` new `MAX_DEEP_READ_URLS = 5`, `web_search.py`): recommendation intent now full-reads up to 5 result URLs (was hardcoded 3), each still via summarization when oversized.
  - Visual ceiling (`config.py` `MAX_SYNTHESIZED_VISUALS` 6 → 7): evidence-driven count now reaches 1–7; ceiling test fixture (+ fundamentals) yields 7, monkeypatched ceiling of 2 yields 2.
  - Files touched: `Backend/app/services/llm/query_rewriter.py`, `Backend/app/services/web_search.py`, `Backend/app/config.py`, `Backend/tests/test_query_rewriter.py`, `Backend/tests/test_context_budget.py`, `Backend/tests/test_pipeline_contract.py`.
  - Deferred (separate architecture, not bundled): PDF/XLSX-unstructured document QA (chunking + embeddings + vector store) and multi-LLM cascade (`specs/12`).

- **B7 evidence hardening — live-web retrieval tuning + visual guarantee ceiling (Phases 0–6)** — done, test-verified (backend **632 tests**, all green, up from 612; frontend build/lint/`npm test` all green — **124 tests**):
  - Phase 0 doc reconciliation (no behavior change): `specs/00` row 7 + architecture line (React 19 + Vite + react-router), `specs/07` header (FR3 selector shipped, FR4 note), `chat.py`/`schemas/chat.py` docstrings (live_web/both live), `Backend/CLAUDE.md` (Redis wired, chat supports live_web/both), `Frontend/CLAUDE.md` TODO(phase-6), `Composer.tsx` comment (ENABLE_LIVE_WEB_SCOPE).
  - Phase 1 config (`Backend/app/config.py`): `MAX_EVIDENCE_CONTEXT_CHARS` (12000), `SUMMARIZE_TRIGGER_CHARS` (6000), `MAX_SYNTHESIZED_VISUALS` (6), `ENABLE_LIVE_WEB_SCOPE` (true).
  - Phase 2 visual ceiling (`langchain_pipeline.py::ensure_visuals`): three literal slices (`[:3]`/`[:5]`/`[:4]`) → `[: get_settings().MAX_SYNTHESIZED_VISUALS]`; web-only fixture now yields 6 visuals, monkeypatched ceiling of 2 yields exactly 2.
  - Phase 3 evidence ranking + budget (new `app/services/llm/context_budget.py`: `estimate_tokens`/`rank_snippet_pairs`/`fit_pairs_to_budget`, fail-closed, citation-aligned): `search_web()` ranks the generic snippet pool by provider score + recency + term overlap and fits to `MAX_EVIDENCE_CONTEXT_CHARS`, disclosing trims via `research_notes`.
  - Phase 4 long-source summarization (new `app/services/llm/evidence_summarizer.py`: chunked map-reduce over `groq_fast_model`, short-text zero-call fast path, truncation fallback): `_tavily_extract()` summarizes content over `SUMMARIZE_TRIGGER_CHARS` instead of hard-truncating (recommendation deep-read inherits it).
  - Phase 5 FR4 scope-disagreement clarification (`query_rewriter.py::EXTERNAL_CONTEXT_RE`/`wants_external_context` + `chat.py::_answer_request`): `own_data` + external-request wording → "Want me to also check live sources for this one?" quick-pick (anti-repeat via the pipeline `_same_question` convention); "Yes, check live sources too" runs as `effective_scope="both"` without mutating the logged scope.
  - Phase 6 live rollout (`chat.py` `ENABLE_LIVE_WEB_SCOPE` kill-switch on `effective_scope`, honest own_data-only fallback): `Composer.tsx` tooltip copy ("Questions are answered using live web search and your uploaded data."), `Frontend/CLAUDE.md` live description, `specs/07` → ✅ Implemented, `specs/00` row 7 → ✅ Implemented.
  - Files touched: `specs/00-overview.md`, `specs/07-news-context-module.md`, `Backend/app/routes/chat.py`, `Backend/app/schemas/chat.py`, `Backend/CLAUDE.md`, `Backend/app/config.py`, `Backend/app/services/llm/langchain_pipeline.py`, `Backend/app/services/llm/context_budget.py` (new), `Backend/app/services/web_search.py`, `Backend/app/services/llm/evidence_summarizer.py` (new), `Backend/app/services/llm/query_rewriter.py`, `Backend/tests/test_pipeline_contract.py`, `Backend/tests/test_context_budget.py` (new), `Backend/tests/test_evidence_summarizer.py` (new), `Backend/tests/test_chat_api.py`, `Backend/tests/test_query_rewriter.py`, `Frontend/src/features/chat/Composer.tsx`, `Frontend/src/features/chat/Composer.test.tsx`, `Frontend/CLAUDE.md` (`composer.css` checked — no live-web-specific disabled style to remove).

- **Pipeline decision-loop hardening (B4 follow-up)** — done, test-verified (backend **160 tests**,
  159 green + 1 pre-existing env failure in `test_window_exhausted_returns_429`, which fails
  identically on the clean tree; frontend build/lint/`npm test` all green — **89 tests**):
  `run_pipeline` is now judge → narrate → guarantee (FR8/FR9, specs/06): an LLM sufficiency
  verdict from real tool outputs, prior-turn context from QueryLogs (prior clarification +
  data digest) so follow-ups like "chart that" resolve instead of looping, an anti-repeat
  backstop (same question twice → one best-effort answer), and a deterministic visual
  guarantee (date→line, categories→bar, grounding table, headline metric, generic market
  graph — values only from real rows/stats/series). The route's hardcoded "One-month stock"
  chart hack is deleted. No contract change — frontend untouched.

- **Pipeline live-hardening round 2** — done, test-verified (backend **204 tests**, 203 green + the same
  1 pre-existing env failure; frontend build/lint/`npm test` all green — **121 tests**): (1) LLM query
  framing — live scopes rewrite the message (merging appended clarification answers) into 1–3 clean
  queries with a community-discussion variant when opinions are sought, fanned out with dedupe, and
  any entity resolves to a market symbol generically (was a 5-name hardcoded list); raw user text
  never hits search as-is. (2) Empty-completion retries for judge and narration (the exact live
  failure in the logs). (3) Requested output shapes ("as a bar chart") honored query→synthesis.
  (4) Numbered-snippet `[n]` citations with phantom-marker stripping. (5) Machine-written `thinking`
  trace on every run (additive field). (6) Sources-table guarantee for web-only answers.
  (7) Clarifications accept free-text replies as well as pills (frontend); empty options are
  backfilled from the judge's evidence-grounded suggestions so both always render together.
  (8) Groq JSON mode for judge/narration/rewriter (HF fallback unaffected) — the live empty-completion
  failures. (9) Tap-to-ask `followups` on answers (additive field + chips).   (10) Inline markdown
  (`**bold**` etc.) renders in prose and clarification questions instead of literally.
  (11) Prose rescue: failed structured narration answers plainly from real evidence
  (confidence 0.35, still guaranteed visuals) instead of the mid-conversation dead-end fallback.
  (12) Chained clarifications send "base - new pick" (prior option suffix stripped), never the
  doubled "base - old - new" bubbles.
  (13) Snippet↔source 1:1 alignment (title+snippet merged with its URL; provider-only kept) so no
  citation is orphaned; bogus "your data" chip on empty previews removed; multi-select pills + Send;
  multi-visual mandate (two honest visuals beat one). Cited money/percent figures become a
  "Figures cited" table plus a normalized bar (bare numbers/years never qualify).
  Specs/06 FR10–FR12; specs/14 §4.3.

- **Scale + trust + feedback batch** — done, test-verified (backend **200 tests**, 199 green + the same
  1 pre-existing env failure; frontend build/lint/`npm test` all green — **121 tests**):
  (1) Groq multi-key rotation (`GROQ_API_KEY`..4, round-robin + failover, 401 retires a key, HF last)
  with `GROQ_FAST_MODEL` tiering for judge/rewriter calls. (2) Parallel evidence branches in
  `/chat` (SQL execution overlaps live search). (3) Evidence-capped confidence (strong 0.90 / thin
  0.65 / none 0.35) so single-source claims never read "High". (4) `POST /chat/stream` SSE with live
  `judging → narrating → visuals` stages driving the process card; quota 429s stay JSON errors.
  (5) `scripts/eval_pipeline.py` (live golden properties, 8/8 passing) + `scripts/review_feedback.py`
  (flagged/fallback review). (6) Chat thread persists across reloads (zustand/persist, capped tail).
  Specs/06 FR13.

- **F6 — Remaining states** — done, test-verified (`npm run build`, `npm run lint`, `npm test`
  all green — **65 tests**, up from 56; dev server boots on 5173):
  - **`EmptyThread.tsx`** (new, `messages/`, specs/14 §6) — the empty thread renders when the
    store has zero messages. **Guests**: a question invite with **NO upload affordance at all**
    (the composer's upload button is already absent for guests — never shown disabled). **Registered
    + no files**: "Add a CSV, PDF, or spreadsheet to get started" with the `UploadPopover` one tap
    away (its dialog/status chips render inline). **Registered + files**: the question invite. The
    component reports `hasData` into the chat store by re-checking the **live** `GET /files` (any
    `completed` file ⇒ `true`); guests are `false` by construction (can't upload, specs/04 FR1).
  - **No-data question messaging** — `chat-store` grows `hasData: boolean | null` (null = unknown);
    `AssistantMessage` routes a **fallback-kind** output through the new **`NoDataMessage.tsx`**
    ("You haven't uploaded any data yet — add a CSV, PDF, or spreadsheet to get started…") whenever
    `hasData === false` — the 07 edge case 2 "say so directly" messaging, never the generic
    "Couldn't produce a reliable answer for that". `UploadPopover` sets `hasData=true` on a
    successful upload. The `EmptyThread` fetch is the null→false/true discovery path.
  - **`ThinkingIndicator.tsx`** (new) — the small **inline** assistant-thinking indicator (three
    bouncing dots, right-aligned under the in-flight user message), rendered by `MessageStream`
    when `pending === 'thinking'` — **distinct from** the F5 cold-start card (which is a named,
    full-width wake-up state); `prefers-reduced-motion` turns dots static.
  - **`MessageStream.tsx`** — when `messages.length === 0` the region renders `EmptyThread`;
    `pending === 'thinking'` renders `ThinkingIndicator`.
  - **CSS:** `message-stream.css` additions — `.empty-thread` (centered invite, icon/title/body/
    action + inline popover), `.message--no-data` (warning-tinted no-data notice, row layout) and
    `.thinking-indicator` (dot bounce, reduced-motion). Tokens only.
  - **Tests:** `EmptyThread.test.tsx` (6 — guest invite + NO upload affordance + hasData false;
    registered-no-files invite; popover opens from the invite; upload flips to question invite;
    registered-with-files invites a question + hasData true; MessageStream wiring shows it on a
    zero-message thread), `MessageStream.test.tsx` (+4 — cold start keeps its named card once a
    user message exists [real composer flow appends the user message before `pending`]; thinking
    indicator under the user message + distinct from cold start; no-data fallback → no-data
    messaging; generic fallback kept when the user does have data). **65 total.**

- **F5 — Composer + ambient controls** — done, test-verified (`npm run build`, `npm run lint`,
  `npm test` all green — **56 tests**, up from 38; dev server boots on 5173):
  - **`Composer.tsx`** (rebuilt) — all of `specs/14` §5: **multiline auto-grow textarea** (5.1,
    capped, scrolls past max; placeholder **"Why did revenue drop last week?"**, Shift+Enter makes
    a newline), **3-way segmented source-scope selector** (5.2 — Your data / Live web / Both,
    always visible, default `own_data`, persisted to localStorage via new `scope-store.ts`
    (zustand persist); Live web/Both are **gated** until B7 — a hint shows "not available yet …
    answers fall back to your own data", the backend's honest fallback answers, never a silent
    switch), **upload button ABSENT — not disabled — for `guest` plans** (5.3; opens the
    `UploadPopover`), **Send disabled ONLY when the input is empty** (5.4 — quota never disables
    it; the 429 flows through as a proper notice). `useChatStore` grew **system messages**
    (`window-exhausted` / `lifetime-cap` / `error`), a `pending` field, and `activeFileName` for
    the user-bubble file chip.
  - **`UploadPopover.tsx`** (new, 5.3): drag-drop zone + browse (`accept=".csv,.pdf,.xlsx"`),
    hint "CSV, PDF, or XLSX · 3MB free / 10MB pro" from `plan`, live `GET /files` list with
    `processing`/`completed`/`failed` status chips and the stored `failed` reason; a completed
    upload becomes the file chip above the next user message (`setActiveFileName`).
  - **Two distinct 429 states** (5.6) rendered **in the message stream** from the store's system
    notices: **`WindowExhaustedNotice`** — transient inline notice with a **live reset-time
    countdown** (input stays enabled), and **`LifetimeCapNotice`** — a permanent-feeling card
    (not a toast) with the inline **name/email/message → `POST /contact`** form and the
    "Thanks — we'll be in touch." reply. Composer send handles the `isQuotaError` branch →
    `applyWindowExhausted`/`applyLifetimeExhausted` + respective notice; other failures surface a
    small transient error notice (generic defensive text via `getErrorMessage`).
  - **Cold start** (5.7) — `MessageStream` renders `ColdStartNotice` ("Waking up the server —
    first load can take up to a minute", determinate-feeling progress) whenever the session's
    **first** send is in-flight (`pending === 'cold-start'`; a module flag marks it done) —
    distinct from the per-message thinking indicator (F6's job).
  - **QuotaChip (5.5)** now renders the live "· resets in 4h" countdown from the client mirror's
    `resetsAt` via a new ticking `useNow` hook (`src/hooks/useNow.ts` + `src/lib/format.ts`
    `formatRemaining` — no `Date.now()` during render, react-hooks/purity).
  - **CSS:** new `composer.css` (segmented control, auto-grow input, icon/send buttons, popover
    layer + dropzone + status chips) and `message-stream.css` additions (window/lifetime notice,
    cold-start sweep, `prefers-reduced-motion`). Tokens only.
  - **Tests:** `Composer.test.tsx` (7 — placeholder + 3-way selector, send w/ scope + quota
    recording + store messages, Enter sends, upload absent for guests, upload popover + size
    hint, scope persistence via localStorage + gated hint, window-429 notice + input stays
    enabled, lifetime-429 → lifetime card notice), `UploadPopover.test.tsx` (3 — list files w/
    status chips + failed reason, 3MB/10MB caps by plan, upload via picker + active-file),
    `MessageStream.test.tsx` (+4 — window-exhausted notice w/ countdown, lifetime card + contact
    form, form POSTs `/contact` + thanks, cold-start), `QuotaChip.test.tsx` (3). **56 total.**
  - **Docs updated in the same change:** `Frontend/CLAUDE.md` §1 (F5 in). Master-plan Part F is a
    planning doc and needs no edit here.

- **F4 — Seven visual components** — done, test-verified (`npm run build`, `npm run lint`,
  `npm test` all green — **38 tests**, up from 30; dev server boots on 5173):
  - **Seven components in `src/components/visuals/`**, each built to `src/lib/schemas/visuals.ts`
    props, lucide-react icons, token-only styling (`visuals.css`):
    **MetricCard** (`metric` — metric-scale value + label + directional change badge; up→success,
    down→danger, flat→muted), **GraphCard** (`graph` — Recharts **line/bar/pie/area** switched on
    `chart_type`, dataset colors from the design tokens so both themes work, `role="img"` +
    `aria-label` per chart), **BusinessSummaryTable** (`table` — real sticky-header table of
    columns/values), **ComparisonCard** (`comparison` — value vs baseline with computed delta +
    proportional group bars), **InsightCard** (`insight` — statement + supporting context),
    **AlertList** (`alert` — level-styled info/warning/critical row), **StatusBadge** (`status` —
    on_track/at_risk/off_track pill + detail), and **UnknownVisualCard** (defensive fallback for an
    unrecognized type).
  - **`VisualCard.tsx`** — the **plain type→component lookup** (specs/14 §8, no interception
    layer): switch on `visual_type`, props casts are the single boundary between the frozen union
    type and each narrowed props shape; `default` → `UnknownVisualCard` so an unknown type degrades
    gracefully.
  - **F3 seam filled:** `VisualCardsGrid.tsx` now renders `<VisualCard>` inside each
    `data-visual-type` card (title header kept; graph/table still span 2 cols).
  - **Tests:** `VisualCard.test.tsx` (8 tests — all 7 types render their component inline; graph
    across line/bar/pie/area; unknown type degrades to the fallback without crashing) + updated
    `MessageStream.test.tsx` (grid title / metric label / chart legend can legitimately share text
    like "Revenue" → `getAllByText`). `src/test/setup.ts` stubs `ResizeObserver` (jsdom lacks it;
    Recharts' ResponsiveContainer needs it).
  - **Docs updated in the same change:** `Frontend/CLAUDE.md` §1 (F4 in); master-plan Part F is a
    planning doc and needs no edit here.
  - **`chat-store.ts`** (new, Zustand) — the message stream's source of truth: a `ChatMessage`
    union (`user` with optional `fileName` chip; `assistant` carrying the raw `PipelineOutput`) +
    `classifyAssistantOutput()` (clarification wins, degraded fallback = empty visuals + confidence
    0, else normal answer). Seeded by F5's composer; the `MessageStream` region renders it.
  - **Four message types (`specs/14` §4)** in `src/features/chat/messages/`:
    **UserMessage** (4.1 — right-aligned bubble, uploaded-file chip **above**, never inside);
    **AssistantAnswer** (4.2 — left-aligned no bubble: `answer` prose → **`VisualCardsGrid`**
    (`repeat(auto-fit, minmax(240px, 1fr))`; `graph`/`table` span 2 cols via `--card--wide`, and
    every card gets `data-visual-type` — the seam F4's type→component lookup consumes) →
    **InsightsStrip** (collapsed by default, hedged "Possible factors" — never "Why this happened",
    `specs/10` §2, grouping root_causes/recommendations/insights) → **TrustFooter** always visible:
    "Show the query" discloses `sql_query` + the raw `data_preview` slice, a confidence `meter`
    rendered **only while the value is bounded 0..1**, and "Flag this answer" wired to the **live**
    `POST /chat/flag` (`api/chat.flagAnswer`; disabled with a tooltip, never hidden, when there's no
    `query_log_id` to write) → **news-context row** "From the web" only when non-empty);
    **ClarificationMessage** (4.3 — accent left edge, `question`, `options[]` **pill buttons**,
    tap sends the option **verbatim** as the next user message via `chat-store`, no answer body/cards/
    footer); **FallbackMessage** (4.4 — neutral "Couldn't produce a reliable answer for that").
  - **`MessageStream.tsx`** now maps store messages → the message components (was the empty region).
    `message-stream.css` new with token-only styling (visual card elevation, hedged insight copy,
    "Possible factors" labelling, accent clarification edge, `prefers-reduced-motion`).
  - **Tests:** `MessageStream.test.tsx` (8 tests — user bubble + file chip above; normal answer with
    visual grid / graph wide span / collapsed insights strip / trust footer / news row; insights
    expand; "Show the query" reveals SQL + preview table; flag calls the live write path;
    flagged-answer finds no log → disabled with tooltip; clarification quick-pick with pill tap
    sending verbatim; fallback notice + no trust footer). Removed the F2 "deliberately empty"
    comment no longer true.
  - **Docs updated in the same change:** `Frontend/CLAUDE.md` §1 (F3 in). Master-plan Part F is a
    planning doc and needs no edit here.

- **F2 — Chat Workspace shell** — done, test-verified (`npm run build`, `npm run lint`, `npm test`
  all green — **22 tests**, up from 19; dev server boots on 5173):
  - **`ChatWorkspace`** (`src/features/chat/ChatWorkspace.tsx`) — the three-region **one-layout**
    shell (specs/14 §3), replacing the F1 `Workspace` placeholder at the same `/app` guard without
    touching routes (`src/App.tsx` now imports it).
  - **Header (56px)** (`ChatHeader`): logo + rail-toggle, BuildifyLabs brand, **quota chip**
    (`QuotaChip` — "N of 4 left" from the client-side `useQuota` mirror; low-window warning state),
    plan badge, **account menu** (identity + sign out), and a "New chat" button.
  - **Chat history rail** (0/280px, `HistoryRail`): sits alongside the stream column on desktop;
    **collapsed by default below 768px** (`useMediaQuery("(max-width: 767.98px)")`, new
    `src/hooks/useMediaQuery.ts`) and rendered as an **overlay slide-in that never pushes content**,
    with a dismiss backdrop. All styling uses the F0 design tokens.
  - **Message stream** (MessageStream: `role="region"`, deliberately empty — "the only place visuals
    render") and **Composer** (pinned to the foot of the stream column) ship as F3 / F5 seams. No
    resizable panel, no fullscreen affordance.
  - **Tests:** `ChatWorkspace.test.tsx` (3 tests — desktop rail open by default, narrow collapsed +
    overlay toggle, overlay opens on narrow) + updated `App.test.tsx` (guards land on the shell:
    New chat, rail, stream regions, user name, plan badge). Removed the F1 dashboard placeholder.
  - **Docs updated in the same change:** `Frontend/CLAUDE.md` §1 (F2 in); master-plan Part F is a
    planning doc and needs no edit here.

- **F1 — Auth screens** — done, test-verified (`npm run build`, `npm run lint`, `npm test` all
  green — **19 tests**, up from 6; dev server on 5173):
  - **Six flows, live API** (`app/routes/auth.py`): Signup, Signin, Google (GIS, button init'd
    lazily against `VITE_GOOGLE_CLIENT_ID`; absent without it), Guest (`device_id` persisted per
    browser so a returning guest reuses their quota), Verify-email (`GET /auth/verify-email?token`),
    Forgot-password, Reset-password (min 8 chars enforced client-side + backend schema layer).
  - **`AuthResponse` handling** through the F0 `useAuth` hook/store; access/refresh persisted via
    `token-storage.ts`; **plan badge** (`guest|free|pro`) component; route guards (`RequireAuth` /
    `RequireGuest` + a token-styled loading screen while the session rehydrates) → authenticated
    workspace.
  - **Generic auth errors shown verbatim** (anti-enumeration, `specs/01` §4): new
    `src/lib/errors.ts` (`getErrorMessage`) passes a string `detail` through untouched; only
    network/non-API failures get an app-authored fallback. Every screen + GIS callback routes its
    error through it.
  - **Workspace placeholder** (`features/dashboard/Workspace.tsx`) — guarded `/app` destination
    showing user + plan badge + sign out; the F2 shell replaces it without touching routes.
  - **Routing** (`App.tsx`): `/` and `*` → `/app`; auth screens under a shared `RequireGuest`
    `AuthLayout`; `/app` wrapped in `RequireAuth`; `useTokenRefresh` re-establishes sessions.
  - **Tests:** `App.test.tsx` (unauthenticated → sign-in; authenticated → workspace + badge),
    `auth-screens.test.tsx` (10 tests: verbatim error display, session commit, stable persisted
    `device_id`, signup/reset min-8 + confirm guards, forgot/reset/verify happy paths + verbatim
    errors), `PlanBadge.test.tsx` (labels + unknown-plan fallback). `setup.ts` adds explicit RTL
    cleanup (no `globals: true`). `tsconfig.app.json` adds `gsi` types; `useAuth` helpers now reset
    the store status to `unauthenticated` on failure so screens/guards never stick on `loading`.
  - **Docs updated in the same change:** `Frontend/CLAUDE.md` §1 (F1 in), `Frontend/docs/type-contracts.md`
    (F1 note + `device_id` required); `Frontend/docs/structure.md` untouched (pre-existing stale
    "nothing decided" intro is out of F1 scope).

- **F0 — Frontend foundations + type-contract freeze** — done, test-verified (`npm run build`,
  `npm run lint`, `npm test` all green; dev server on 5173):
  - **Stack decided once** (master plan Part F): **react-router** · **Zustand** (stores in
    `features/*`) · thin **fetch wrapper** (`src/lib/http.ts`) · **plain CSS with custom-property
    design tokens** · **Recharts** · **lucide-react** · **Google Identity Services** (script in
    `index.html`) · **Vitest + React Testing Library** (tests).
  - **Structure** `src/{api,types,lib/schemas,components,features,hooks}` created; boilerplate
    `App.tsx`/`App.css`/`index.css` replaced (`App.tsx` is a router-wired placeholder awaiting F1).
  - **Design tokens (`specs/14` §7 roles)** in `index.css`: `surface-page|card|raised`,
    `text-primary|secondary|muted`, `accent`, `success|danger|warning`, type scale 13px captions /
    15px body / 22px metric / 12px floor; light + dark themes; values are **placeholders flagged for
    Figma reconciliation** (roles are the contract, values are follow-up).
  - **Type-contract freeze (shared gate):** `src/lib/schemas/visuals.ts` landed as the
    per-`visual_type` props **single source of truth** — discriminated union `VisualProps` +
    `isVisualType` runtime guard, exactly the 7 B4 types. Auth/upload/chat/payment/contact types
    mirrored in `src/types/` (upload now includes the B3 `error` field).
  - **API seam** `src/api/{auth,files,chat,contact,payments}.ts`: auth/files/chat/contact hit the
    **live** backend; payments is **mocked** to the contract shape (one-file swap at F7). Components
    call `src/api/*` only — never `http` directly — so mock↔real is a one-file change.
  - **Auth/token plumbing:** token-storage decision documented and implemented
    (`src/lib/token-storage.ts`) — **access token in memory**, refresh token in localStorage
    (backend expects a Bearer header; localStorage holds a risk for business data). `useAuth`,
    `useTokenRefresh` (60 min access / 7 d refresh), `useQuota` (client-side rolling-window tracker
    mirroring `specs/02`; the backend 429 stays authoritative) + Zustand stores in
    `features/{auth,chat}`. Quota hook avoids `Date.now()` during render (react-hooks/purity);
    the live countdown label is the F5 chip's job from the exposed `resetsAt` timestamp.
  - **Docs updated in the same change:** `docs/type-contracts.md` (Upload `error` field + visuals.ts
    landed note), `docs/structure.md` (stale "resets at UTC midnight" quota line → rolling-window /
    `contact_form` 429 distinction), `Frontend/CLAUDE.md` (F0 stack, env vars, token storage),
    `index.html` (title + GIS script), master plan (F0 status + contract-freeze line).
  - **Tests (Vitest harness):** `visuals.test.ts` (exactly the 7 types; guard rejects the old 9-type
    values), `token-storage.test.ts` (access in memory / refresh persisted / clear), `App.test.tsx`
    (placeholder smoke). **6 tests, all green.**

- **B4 — End-to-end `POST /chat`** — done, test-verified (**149 tests**, up from 112):
  - **`langchain_pipeline.py` migrated to the `specs/06` §3 contract** — 7 real `visual_type`s
    (`metric`/`graph`/`table`/`comparison`/`insight`/`alert`/`status`) with `props: Dict` (no `chart_data`);
    `confidence` `Field(ge=0.0, le=1.0)`; `clarification: Optional[ClarificationRequest]` alternate mode;
    `run_pipeline(..., source_scope="own_data", company_name=None)` (old `include_news` gone); mutable default
    `news_context: list = []` → `None`; `SYSTEM_PROMPT` teaches the 7 types + hedged causal language
    (`specs/10` §2); `_truncate_rows` caps prompt rows at 50 (edge case 6).
  - **`app/routes/chat.py`** (new): `POST /chat` — `rate_limiter` (quota) → SQL prompt from the user's **real**
    uploaded columns (`get_table_columns` → `build_data_schema`) → LLM → `clean_sql_response` → `sanitize_sql`
    → user-scoped `execute_sql` → deterministic pandas stats (`specs/11` §3.1, `app/services/data/stats.py`) →
    `run_pipeline` → `PipelineOutput`. `INVALID_QUERY` → graceful fallback message. Every request+response
    written to `QueryLogs` (incl. fallbacks). `source_scope` = `own_data` only (B7 deferred). **Trust
    requirements (`specs/10` §2) built in:** `sql_query` + `data_preview` on the response (real "show the
    query"), hedged language, `clarification` as a working mode.
  - **`POST /chat/flag`** — own-only flag (other user → 404) setting new `QueryLogs.flagged` column; migration
    `b4code0000_query_logs_flag.py` (`alembic heads` = `b4code0000`).
  - **`app/services/data/executor.py::get_table_columns()`** — introspects real per-file columns
    (information_schema → PRAGMA fallback); the `sales/customers/orders` placeholder is now only a fallback.
  - **`rate_limiter` fix surfaced by `/chat`:** the atomic quota `UPDATE` needed
    `synchronize_session=False` — its ORM evaluate path compared a SQLite-loaded naive `window_started_at`
    against the tz-aware `now` and crashed on commit (SQL unchanged; Postgres unaffected).
  - **Tests:** `test_stats.py` (deterministic stats incl. datetime/NaN), `test_pipeline_contract.py` (7 types,
    bounded confidence, truncation, fallbacks, clarification mode, mutable-default regression),
    `test_chat_api.py` (10 e2e: happy loop, clarification, invalid query, source_scope fallback, flag own/other,
    quota 429 window + lifetime, no-data response). `conftest.py` adds dummy `GROQ_API_KEY`/`HF_API_KEY` (the
    Groq client is constructed at import time).
  - **Docs updated in the same change:** `specs/05`, `06`, `10`, `11`, `00` module map + build order,
    `implementation-plan-master.md` (contract table, module state, B4 section, risks #5–6), `Backend/CLAUDE.md`,
    `Frontend/CLAUDE.md` (chat + upload now live), `Frontend/docs/type-contracts.md` Chat section (7 types),
    `Backend/docs/known-gaps.md`.

- **B3 — File upload + minimal ingestion** — done, test-verified (112 tests):
  - **`app/routes/files.py`** (new): `POST /files/upload` → **202** `FileResponse`; `GET /files`,
    `GET /files/{id}` (own-only, else 404). Row created up front (`processing`); ingestion either
    sets `completed` (with the per-user table ref in `pinecone_namespace`, until Pinecone) or
    `failed` + trimmed `error` — never stuck on `processing` (`specs/04` edge case 1).
  - **`file_validator.py`** — now `400` for **0-byte** files; type check tightened to a **per-type
    EXT↔MIME mapping** so a `.csv`+`application/pdf` mismatched pair is `415` by design (`specs/04`
    §4). Guest/invalid plan → 403; free 3MB / pro 10MB → 413 unchanged.
  - **Storage backend (gap #4 resolved)** — `app/services/data/storage.py`: **local disk for dev**
    (`UPLOAD_DIR` config), object store for prod; the module is the swap seam. Raw uploads persist
    as `<user_id>/<upload_id><ext>` (never the caller's filename → no path traversal).
  - **`app/services/data/parser.py`** (new): pandas CSV parse (BOM/utf-8/latin-1 fallback, ragged-row
    tolerant, empty/`ParserError` → clean `ValueError`) → defensive clean (columns normalized to
    snake_case, all-NaN rows and full-dupe rows dropped, string→date + ₹/`,`-currency coercion) →
    **drop-and-recreate the user's typed data table** via B2's `user_data_table_name()` (columns
    Integer/Float/Boolean/DateTime/Text by pandas dtype) and bulk-insert. A fresh upload **replaces**
    the user's data table (one-active-file scope, `specs/04` §4). `.xlsx`/`.pdf` → `failed` with
    reason "not supported yet".
  - **`FileUpload.error` column** (String(500)) + migration `b3code0000_file_upload_error.py`
    (`alembic heads` = `b3code0000`; adds `ALTER TABLE file_uploads ADD COLUMN error VARCHAR(500)`);
    `FileResponse` gains `error`. `main.py` wires the files router.
  - **B2↔B3 co-design delivered:** `execute_sql()` runs against B2's tables; small smoke script proved
    B2's `execute_sql` returns correct aggregates (SUM/GROUP BY) on B3's ingested table.
  - `specs/04` status/§4/Frequirements/§5/§6 checkboxes, `specs/00` module map + gap #4, the master
    plan's module state + upload contract + risks, `docs/known-gaps.md`, and `Backend/CLAUDE.md`
    updated in the same change.
- **B2 — SQL generation + execution + user-scoping** — done, test-verified:
  - **`sql_generator.py`** (`app/services/llm/sql_generator.py`): `clean_sql_response()` now
    extracts a single bare SQL statement from plain / fenced (```sql```) / prose-wrapped model
    output by keeping the longest prefix that parses as exactly one statement (text-cleanup only).
    `INVALID_QUERY_SENTINEL` + `is_invalid_query()` (normalized exact match) replace the hardcoded
    sentinel. `build_data_schema(table, columns)` + `build_sql_prompt(query, schema=...)` make the
    prompt schema dynamic; `DEFAULT_DATABASE_SCHEMA` (`sales/customers/orders`) is now only a
    documented fallback until B4 feeds real per-file column metadata.
  - **`app/services/data/executor.py`** (new): `user_data_table_name(user_id)` — deterministic
    per-seuser data table (`user_<uuidhex>_data`), the **co-designed B3 storage contract**;
    `assert_user_scoped(query, user_table)` — AST walk rejects (403) any non-CTE table reference
    outside the caller's namespace (shared app tables, another user's table, foreign schemas);
    `execute_sql(query, db, user_table) -> list[dict]` — composes `is_invalid_query` →
    `sanitize_sql` → `assert_user_scoped` → execute; empty result → `[]`; `InvalidQueryError` on
    the sentinel; Postgres execution errors (hallucinated columns) → clean 422 with rollback.
  - **The blocking user-scoping gap (`specs/05` §5.5) is closed**: structural per-user tables
    (B3 creates them) + post-generation validation. A generated query can never read another
    user's rows.
  - **Test suite established** at `Backend/tests/` (76 tests pass, `python -m pytest` from
    `Backend/`): `clean_sql_response` (plain/fenced/prose/edge), `sanitize_sql` regression
    (write/DDL incl. CTE smuggling, forbidden functions), `assert_user_scoped`, and `execute_sql`
    end-to-end on in-memory SQLite (rows, empty, sentinel, 403 foreign table/write, 422 bad
    column).
  - `specs/05` status, §3 contract, §5 edge cases + §6 checkboxes updated in the same change;
    `specs/00` module map + cross-cutting gap #5, `docs/conventions.md` (scoping invariant),
    `docs/known-gaps.md`, `Backend/CLAUDE.md` invariant, and the master plan's module state.
- **B1 — Quota rewrite + contact flow** — done, smoke-verified:
  - **`User` model** (`app/db/models/user.py`): added `questions_in_window`,
    `window_started_at`, `questions_lifetime`; removed `queries_today`, `last_reset`. Migration
    `alembic/versions/b1code0000_quota_rolling_window.py`.
  - **`usage.py`** now the single source of truth for the window rule: `QUOTA_WINDOW` /
    `window_elapsed_clause()` (SQLAlchemy form for the atomic UPDATE) / `window_reset_at()`.
    Old `reset_daily_usage_if_needed` removed.
  - **`rate_limiter.py`** rewritten: everyone gets 4-per-6h + 100-lifetime, no `plan` branching;
    one atomic `UPDATE ... WHERE ... RETURNING` over **both** counters + the roll condition (the
    same statement rolls `window_started_at` and resets `questions_in_window` when 6h elapse, so a
    count can't land against a just-rolled window). Emits `429 {detail}` (window, with reset time)
    and `429 {detail, contact_form: true}` (lifetime) — raised as `QuotaLimitExceeded`, handled
    app-wide in `main.py` so the body matches the `specs/02` §3 contract exactly.
  - **`guest_auth.py`** — dropped `GUEST_DAILY_LIMIT` and the old sign-in daily check; guests use
    the same window logic tracked by `device_fingerprint` (best-effort lifetime — accepted).
  - **`plan_checker.py`** — stays dormant; now logs a warning on unrecognized `plan` values.
  - **`POST /contact`** — `{name,email,message}` → email via existing `email_sender.py` async SMTP
    to new required `CONTACT_FORM_RECIPIENT_EMAIL` config; no verification (low-stakes lead capture).

## What's after

F0–F6 (foundations, auth, workspace shell, message types, seven visual components, composer +
ambient controls, remaining states) and backend B0–B4 (auth/quota/upload/chat core loop) are
done — this was the 🚩 CHECKPOINT (`specs/00` §7: define a "worth continuing" bar, e.g. % of
first-time users asking a 2nd question in-session, and put the core loop in front of real users
before any POST-CHECKPOINT phase). Note: that bar was never formally defined, and
POST-CHECKPOINT-adjacent work has shipped anyway (B7 evidence hardening Phases 0–6, 4-query
framing + whole-pool budgeting + 5-URL deep reads + 7-visual ceiling, visual-empty production
fix, pipeline package split, and this reliability-hardening pass) — proceeding on the explicit
decision that hardening the core loop does not wait on the real-user gate; the gate still applies
to net-new product scope (payments, document-QA retrieval, multi-LLM cascade). Next: define the
"worth continuing" bar and put the core loop in front of real users.

## Blocked / deferred

- **Spec-01 completeness (single-use reset tokens; resend-verification endpoint)** — ⏸ decided OUT
  of B0 at execution (plan: "decide in/out at execution"; known-gaps, not on critical path).
- **Phase B9 payments / F7 upgrade UI** — ⏸ paused (`specs/03`).
- **PDF/XLSX unstructured document QA** (parse → chunk → embed → Pinecone → retrieve → synthesize;
  `specs/04`/`specs/08`) — ⏸ deferred, separate multi-week architecture initiative, not bundled
  with retrieval hardening. CSV upload → per-user table → NL→SQL path is the working scope.
- **Multi-LLM provider cascade** (`specs/12`) — ⏸ deferred, separate architecture decision.

## Important decisions

- **Frontend stack (F0, locked):** react-router · Zustand · thin fetch wrapper (`src/lib/http.ts`) ·
  plain CSS + custom-property tokens · Recharts · lucide-react · Google Identity Services ·
  Vitest/RTL. Applied consistently app-wide; see `Frontend/CLAUDE.md` §4.
- **Token storage (F0):** access token **in memory**, refresh token in localStorage. Backend expects a
  Bearer header (cookie would need a backend change); localStorage is an XSS-read vector for business
  data, so the access token never touches it (`src/lib/token-storage.ts`). `useTokenRefresh`
  rehydrates the in-memory access token from the stored refresh on load.
- **Quota display is client-side mirror (F0):** the backend has no GET-quota endpoint, so `useQuota`
  tracks the rolling window/lifetime client-side (`features/chat/quota-store.ts`, persisted) and is
  kept honest by real API outcomes (successful `/chat` → `recordQuestion`; 429 body → the two
  `applyWindowExhausted`/`applyLifetimeExhausted` states). The backend 429 remains authoritative.
- **Per-user data tables (B2↔B3 co-design):** each user's uploaded data lands in a dedicated table
  `user_<uuid-hex>_data` (`executor.user_data_table_name()`). User-scoping is *structural* (that
  table only ever holds the owner's rows) plus post-generation `assert_user_scoped()` validation —
  resolving the "inject `WHERE user_id` vs per-user table" question in favor of per-user tables
  (`specs/05` §5.5). Also satisfies `specs/08` FR5 later.
- **Dynamic schema (B4, resolved):** `get_table_columns()` introspects real, typed per-file columns into
  the `/chat` prompt via `build_data_schema()`; the `sales/customers/orders` placeholder is only a
  documented fallback. B3's per-user tables feed it.
- **Upload storage backend (B3):** local disk for dev (`app/services/data/storage.py`,
  `UPLOAD_DIR`); object store (S3) for prod — module is the swap seam (resolved gap #4).
- **Per-type EXT↔MIME validation (B3):** the "double-check" is enforced per file type (`.csv` →
  `text/csv`, etc.), so a mismatched pair like `.csv`+`application/pdf` is a deliberate `415`
  (`specs/04` §4).
- **One active data file per user (B3):** a fresh upload **replaces** the user's per-user data
  table; `pinecone_namespace` temporarily holds the per-user table name as the storage ref until
  the real Pinecone namespace is wired.
- `.xlsx`/`.pdf` uploads pass validation but land `status="failed"` with a stored reason (parsing
  beyond CSV deferred); raw file is still persisted.
- **7-type visual contract (B4, frozen; visuals.ts landed in F0):** `visual_type` is
  `Literal["metric","graph","table","comparison","insight","alert","status"]` with `props: Dict`;
  `src/lib/schemas/visuals.ts` (frontend) is now the landed authoritative per-type props source of
  truth (discriminated union + runtime guard) — backend only constrains the type values.
  `confidence` is `Field(ge=0.0, le=1.0)`.
- **LLM never does arithmetic (B4, `specs/11` §2):** `stats.py` computes averages/totals/growth/ratios
  deterministically in pandas; `run_pipeline` receives them as `computed_numbers` to **narrate**, never
  calculate. `GROQ_MODEL` interim = `llama-3.3-70b-versatile`; retires **2026-08-16** → pick a durable model in B5.
- **Trust traceability (B4, `specs/10` §2):** `PipelineOutput.sql_query` + `data_preview` carry the exact SQL
  and raw row slice end-to-end (filled by the route, never the LLM); `QueryLogs` written on every `/chat`;
  `POST /chat/flag` sets `QueryLogs.flagged` (own-only).
- Quota constants (`4` / `6h` / `100`) are a **module decision** in `app/utils/usage.py` (config only
  defines auth rate-limit *counts*); single source of truth for the window rule stays in one place.
- Guest lifetime cap is best-effort (`device_fingerprint`) — accepted tradeoff, `specs/02` §5.
- In-memory per-instance auth limiter is MVP-acceptable; swap to Redis (shared store) with B7.
- Environment gap found: `requests` needed by `google-auth` is not in `requirements.txt` (installed
  only in a `/tmp` temp venv for verification — not modified). Tracked; not part of B1. `pandas`
  **is** now in `requirements.txt` (B3 parser).

## Tests / verification (this run)

**Backend** — `python3 -m pytest` run from `Backend/` on 2026-09-11 — **658 passed**
(Python 3.12; `conftest.py` supplies dummy env vars so no `.env` is needed; async scenarios run
via `asyncio.run`). Includes reliability hardening Phases 1–3 plus answer token streaming (+9
streaming tests) on top of the 638 baseline (which itself needed one
`_DuckDuckGoParser._pending` → `_pending_pair` green-fix for a `HTMLParser` internal collision
on Python 3.12).

**Frontend** — `npm test -- --run` run from `Frontend/` on 2026-09-12 — **21 test files,
139 tests, all passed** (Vitest + RTL, jsdom); `npm run build` ✅, `npm run lint` ✅.

## Last updated

2026-09-12 (Whole-app light/dark mode with persisted toggle complete; live counts backend 658 / frontend 139).
