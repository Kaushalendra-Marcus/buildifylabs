# BuildifyLabs — STATUS

Tracks one-task-at-a-time progress against
[`implementation-plan-master.md`](./implementation-plan-master.md). Source of truth for product
requirements is `specs/`; re-read only the relevant plan/phase before each run.

Legend: ✅ completed · ⚠️ partial · ⛔ blocked · ⏸ deferred/paused

## Current task

**Phase F6 — Remaining states** `[IMMEDIATE]` (master plan Part F; `specs/14` §6): **empty thread,
guest** (invite a question — no upload affordance at all), **empty thread, registered, no files**
(invite an upload "Add a CSV, PDF, or spreadsheet to get started"; a no-data question routes through
the no-data messaging, not a generic empty), **assistant "thinking"** indicator (small inline
indicator under the user's message — distinct from the F5 cold-start). Depends on: F2–F5.
Acceptance: all `specs/14` §6 states present.

## Completed tasks

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

F0/F1 completed the frontend foundations + type-contract freeze + the six auth flows; B4 completed
the backend core loop; F2 landed the Chat Workspace shell; F3 landed the four message-stream
components against the live `POST /chat`/`/chat/flag` API; F4 landed the 7 visual components into
the F3 grid seam (plain lookup + unknown-type fallback); F5 landed the composer + ambient controls
(specs/14 §5) against the live `/chat` + `/files*` + `/contact` API. Next: the last frontend
pre-checkpoint phase **F6 — remaining states** (empty-thread guest / registered-no-files, and the
assistant "thinking" indicator) — building on F2–F5 against the live
`src/api/*` seam. Before any POST-CHECKPOINT
phase (B5+ / F7+): **define the "worth continuing" bar** (e.g. % of first-time users asking a 2nd
question in-session) and put the core loop in front of real users (**🚩 CHECKPOINT**, `specs/00` §7).

## Blocked / deferred

- **Spec-01 completeness (single-use reset tokens; resend-verification endpoint)** — ⏸ decided OUT
  of B0 at execution (plan: "decide in/out at execution"; known-gaps, not on critical path).
- **Phase B9 payments / F7 upgrade UI** — ⏸ paused (`specs/03`).
- **Phases B5–B8, F7–F9** — 🔴 post-checkpoint (`specs/00` §7); do not start before real-user checkpoint.
- **B4 → frontend F8 live source-scope** — 🔴 gated on check B7.
- **B7 `source_scope` beyond `own_data`** — needs Pinecone+Redis (`specs/07`).

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

**Backend** — `pytest` run from `Backend/` — **149 tests, all green** (temp venv `/tmp/opencode/blvenv`,
Python 3.12; `conftest.py` supplies dummy env vars incl. `GROQ_API_KEY`/`HF_API_KEY` so no `.env`
is needed; no pytest-asyncio — each async scenario runs via `asyncio.run`):

- **B1–B3 modules (unchanged):** auth, quota (incl. `synchronize_session=False` atomic UPDATE now
  exercised by `/chat`), upload validator/parser/files e2e — all still pass.
- **`test_stats.py`** (new): `compute_statistics` — averages/totals/mins/maxs on numeric cols
  (`id` excluded), totals ratios, period-over-period growth %, `<2` periods → no `growth_pct`,
  ISO-string and `datetime` date drivers, NaN/empty/header-only inputs.
- **`test_pipeline_contract.py`** (new): 7 `visual_type`s + `props` (no `chart_data`); bounded
  `confidence`; `clarification` mode; SYSTEM_PROMPT hedged-language + 7-type teaching; 50-row
  truncation with summarizing note; `run_pipeline` fallbacks (bad JSON / empty visuals / exception →
  fallback with `reason`); mutable-default regression.
- **`test_chat_api.py`** (new, file-backed SQLite + seed users/tables, monkeypatched `generate_response`):
  happy `/chat` loop returns `PipelineOutput` with SQL + data_preview; clarification mode;
  `INVALID_QUERY` → graceful fallback still logged; non-`own_data` scope fallback; no-uploaded-data
  response; flag own answer lands on the `QueryLogs` row; flagging another user's log → 404; quota
  429 on window exhaustion and on lifetime cap.
- **Migration check:** `alembic heads` = `b4code0000` (chain `9eec775a77e0 → b1code0000 → b3code0000 → b4code0000`).

**Frontend (F1–F5)** — from `Frontend/`: `npm run build` (tsc -b + vite build) ✅, `npm run lint` ✅,
`npm test` ✅ (**56 tests**, up from 38), `npm run dev` boots on **http://localhost:5173** ✅.
Vitest harness (`vitest.config.ts`, jsdom, `src/test/setup.ts` with jest-dom + explicit RTL cleanup +
ResizeObserver stub for Recharts):
`visuals.test.ts` (7 types), `token-storage.test.ts` (access in memory / refresh persisted / clear),
`App.test.tsx` (routing guards: unauthenticated → sign-in; authenticated → F2 shell with rail +
stream + New chat + badge), `auth-screens.test.tsx` (10 tests), `PlanBadge.test.tsx` (labels +
unknown-plan fallback), `ChatWorkspace.test.tsx` (3 tests — desktop rail open by default;
narrow <768px collapsed by default + overlay toggle; narrow overlay opens via header toggle;
matchMedia stubbed since jsdom lacks it), **`MessageStream.test.tsx`** (12 tests — the four
`specs/14` §4 message types + the F5 §5.6 system notices + §5.7 cold start: user bubble + file chip
above; normal answer w/ visual grid + graph-wide span + collapsed insights strip + trust footer +
news row; insights expand; "Show the query" reveals SQL + preview table; flag hits the live
`/chat/flag` write path; flag disabled with tooltip when no query log; clarification pill tap sends
verbatim; fallback notice + no trust footer; window-exhausted notice w/ reset countdown; lifetime
cap card + contact form; form POSTs `/contact` + thanks; cold-start named state),
**`VisualCard.test.tsx`** (8 tests — the plain type→component lookup renders each of the 7 types
inline: metric value + change badge, graph line/bar/pie/area, table with sticky headers, comparison
value/delta/group bars, insight text+context, alert level styling, status pill; unknown type degrades
to the fallback), **`Composer.test.tsx`** (7 — §5.1 auto-grow + real placeholder, §5.2 scope
segments default/persist + gated hint, §5.3 upload absent for guests + popover size hint, §5.4 send
disabled only when empty + Enter sends, §5.6 window-429 → notice + input stays enabled, lifetime-429
→ lifetime card notice), **`UploadPopover.test.tsx`** (3 — list w/ status chips + failed reason,
3MB/10MB caps by plan, upload via picker + active-file), **`QuotaChip.test.tsx`** (3 — §5.5 "N of 4
left" + live "· resets in", low-warning state, no countdown before first question).

## Last updated

2026-08-09 (F5 complete — composer + ambient controls in `src/features/chat/`: auto-grow text input
with real-example placeholder, persistent 3-way source-scope selector (Live web/Both B7-gated),
upload button absent for guests + `UploadPopover` (drag-drop, CSV/PDF/XLSX, 3MB/10MB hints, status
chips + failed reason), send disabled only when empty, the two distinct 429 states (window-exhausted
inline notice with live reset countdown / lifetime-cap permanent card with inline `/contact` form),
QuotaChip "· resets in 4h" live countdown, and the cold-start first-load state — via
`scope-store.ts`, `useNow`, `composer.css`; 18 new tests; see `git diff` for the exact change set)
