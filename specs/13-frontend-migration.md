# 13 — Frontend Consolidation (Tambo Removal & Next.js Migration)

**Status:** 🔶 In progress — decision made and documented here; physical file migration and Tambo
code removal are pending (see §4). This file exists so the decision and its open items survive even
though the mechanical work isn't finished yet.

---

## 1. What happened

Two frontend efforts existed in parallel without either being aware of the other: an unmodified
Vite/React scaffold inside `buildifylabs/Frontend` (nothing built), and a substantially-built
Next.js app in a separate sibling folder
(`Buldify-Labs-AI-Business-Intelligence-Workspace`) — a real chat interface, a resizable dashboard
workspace, and seven working visual components (`MetricCard`, `GraphCard`, `ComparisonCard`,
`InsightCard`, `AlertList`, `BusinessSummaryTable`, `StatusBadge`), built on **Tambo AI**, a
third-party SaaS that intercepts LLM tool-calls and auto-renders matching React components.

**Decision: the Next.js app is the real frontend.** The Vite scaffold is retired. Tambo AI is
removed entirely — good pattern (LLM decides what to render), wrong vendor for a product meant to
become real: a client-exposed API key (flagged as issue #1 in that project's own recent audit,
`artifact.md`), a load-bearing dependency on a niche third-party SaaS, and a recurring cost on top
of the LLM costs already being paid elsewhere. The seven components themselves are good, reusable
UI regardless of what glues them together — only the routing/glue layer (Tambo's tool-call
interception) is being replaced with something owned outright: the backend returns which visual type
to show, the frontend does a plain lookup to the matching component.

## 2. Replacement architecture

- Chat state moves from Tambo's `useTamboThread()` to a local Zustand store (`chat-store.ts`),
  consistent with the existing `workspace-store.ts` / `query-groups-store.ts` pattern already used
  elsewhere in this codebase.
- The mock `/api/tambo/message` route is replaced by `/api/chat`, which — until the real FastAPI
  `/chat` endpoint exists (`06`, `11`) — calls the frontend's own existing `groq.ts` service
  directly as an interim measure. **This is temporary.** Per `12-llm-orchestration.md` §2, the
  frontend should ultimately never call an LLM provider directly at all — once the backend's
  `/chat` route ships, this route becomes a thin proxy to it instead, and the frontend's direct
  Groq integration goes away.
- Per-component prop schemas (previously duplicated — a "live" set used for Tambo's own runtime
  validation, and a separately-imported set the seven components actually type themselves against)
  are consolidated into one file, `src/lib/schemas/visuals.ts`, with no Tambo dependency.
- The seven components move from `src/components/tambo/` to `src/components/visuals/` — the old
  path name is a Tambo leftover and actively misleading once Tambo is gone.

## 2.5 Chart/icon rendering moves from hand-rolled to library-based

**Decision, made during a pre-migration code review, folded into this same pass since every
component is already being touched:** replace hand-rolled SVG chart and icon code with maintained
libraries, for reliability — hand-rolled coordinate/trig math is exactly the class of code most
prone to the kind of subtle edge-case bugs already found elsewhere in this codebase (`artifact.md`
ISSUE-10's JSON-extraction edge case is the same underlying pattern: reinvented-the-wheel code that
missed a case a maintained library already handles).

- **`GraphCard`'s line/bar/pie/area charts** — currently ~250 lines of manual SVG `viewBox`
  coordinate math and trigonometry (pie slice angles via `Math.cos`/`Math.sin`) — move to
  **Recharts** (`<LineChart>`, `<BarChart>`, `<PieChart>`, `<AreaChart>`). Recharts already handles
  edge cases the hand-rolled version has to rediscover one at a time: single-data-point series,
  negative values, empty series, responsive resize.
- **Every hardcoded inline `<svg><path>` icon** — currently duplicated independently across
  `MetricCard`, `InsightCard`, `AlertList`, `StatusBadge`, and `BusinessSummaryTable` (five separate
  copies of near-identical Heroicons-style path data) — moves to **`lucide-react`**, imported per
  icon instead of hand-copied.
- **`BusinessSummaryTable`'s manual sort logic** is a reasonable candidate for **TanStack Table**
  if/when filtering or pagination complexity grows — not required for this pass, noted as a
  follow-on rather than blocking it.
- This does **not** change `06-ai-insight-pipeline.md`'s `visual_type` contract or `props` shape —
  those describe *what* each component receives, not *how* it renders internally, and `06` already
  defers to `src/lib/schemas/visuals.ts` as the source of truth for exactly this reason. Swapping
  the rendering internals of an existing type doesn't touch the backend contract at all; only
  adding a genuinely new `visual_type` would.

## 3. A contract mismatch this surfaced, that needs closing

`06-ai-insight-pipeline.md`'s `visual_type` field currently lists 9 fictional types (`line_chart`,
`bar_chart`, `pie_chart`, `kpi_card`, `heatmap`, `funnel_chart`, `india_map`, `anomaly_chart`,
`ai_summary`) that don't match what's actually built in the frontend. **The real, working frontend
is canonical now** — `06` needs its `visual_type` enum and `VisualOutput` shape rewritten to match
the 7 real component types (`metric`, `graph`, `table`, `comparison`, `insight`, `alert`, `status`)
before the backend's `/chat` route gets built, or it'll be built against a contract the frontend
can't render. This is tracked as open in `06`'s own file — check there for current status rather
than assuming this note means it's already done.

## 4. Open items (physical migration, not yet executed)

These are blocked on tooling, not on decisions — the decisions are made:

- **Move the Next.js app's source/config files into `buildifylabs/Frontend`**, archiving the old
  Vite scaffold rather than deleting it. `node_modules`, `.next` (build output), and
  `tsconfig.tsbuildinfo` are excluded from the move — regenerate with `npm install` / `npm run
  build` at the new location rather than moving them (they're too large for a bulk-move operation
  to handle reliably, which is what stalled this originally).
- **The Next.js app has its own separate `.git` history**, distinct from `buildifylabs`' own repo.
  Decision: don't merge histories — move the nested `.git` folder itself out to an archive path
  (not delete) so it's disconnected but recoverable, leaving `Frontend/` as a plain directory ready
  to be added into `buildifylabs`' existing repo whenever `git add` is next run there.
- **A Tambo AI API key was found committed in a `.env` file.** Do not carry it forward into the
  migrated app. It needs to be rotated (regenerated at the Tambo dashboard, if that account is kept
  for any reason) and scrubbed from that repo's git history (`git filter-repo` or equivalent) —
  neither of these are things a filesystem-only tool can do; they need to be run directly.
- Once moved, remove the Tambo-coupled files (`TamboProviderWrapper.tsx`, `src/lib/tambo/`,
  `src/app/api/tambo/`, `useTamboWorkspaceIntegration.ts`) and the `@tambo-ai/react` /
  `@valibot/to-json-schema` / `valibot` dependencies from `package.json` — archive rather than
  delete, consistent with how the Vite scaffold and `.git` history are being handled.
- **Add `recharts` and `lucide-react` to `package.json`**, and work through the seven components
  (now at `src/components/visuals/`, per §2.5) replacing hand-rolled chart/icon code with them.
  Same pass as the Tambo removal, not a separate migration — every component file is already being
  opened and edited for the path move and the schema consolidation (§2), so this is the efficient
  point to also swap the rendering internals rather than doing a second pass later.

## 5. Acceptance Criteria

- [ ] `buildifylabs/Frontend` contains the real Next.js app; the Vite scaffold is archived, not
      lost.
- [ ] No file in the active tree references `@tambo-ai/react`, `NEXT_PUBLIC_TAMBO_API_KEY`, or any
      `src/lib/tambo/*` import path.
- [ ] The previously-exposed Tambo key is rotated and scrubbed from git history.
- [ ] `06-ai-insight-pipeline.md`'s `visual_type` enum matches the 7 real component types.
- [ ] `/api/chat` (interim) is clearly marked as temporary in code comments, pointing at the real
      backend `/chat` route as its replacement once `06`/`11` ship.
