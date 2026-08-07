# CLAUDE.md — Frontend

Guidance for Claude (or any agent) working in `Frontend/`. Read this before making changes.

## What this is

Frontend for an **AI Business Intelligence Copilot**: a user uploads business data (CSV/PDF/XLSX),
asks questions in plain English in a chat-style UI, and gets back a chart, a plain-English
explanation, and recommendations.

**Current state: this is still the unmodified `npm create vite -- --template react-ts` scaffold.**
`src/App.tsx` is template boilerplate (counter button, Vite/React logo links) — nothing product-
specific has been built yet. Per `../specs/00-overview.md`'s module status table, the frontend is
explicitly marked "❌ Not started (Vite/React scaffold only)." Treat almost everything below as
"what to build," not "what exists."

The actual product spec — API contracts, data shapes, and required UI flows — lives in
**`../specs/`**, one file per backend module (`00-overview.md` first). The frontend has no spec
files of its own; it must be built to match the backend's contracts documented there. Read the
relevant spec before building the corresponding screen, and check `../Backend/CLAUDE.md` for the
backend's current implementation status — several backend modules referenced below are only
partially built or not yet wired to a route, which directly limits what the frontend can actually
call today.

## Tech stack (as configured, nothing more)

- **React 19** + **TypeScript** (`~6.0.2`) + **Vite 8**
- **ESLint 9** with `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`
- No router, no state management library, no CSS framework, no HTTP client, no component library,
  no chart library — **none of these are chosen yet**. Pick deliberately per the needs of each
  screen rather than defaulting to something because it's familiar; nothing here constrains the
  choice, but check for team preference before introducing a dependency the rest of the app doesn't
  already use.

## Scripts

```bash
npm install
npm run dev       # Vite dev server — defaults to http://localhost:5173
npm run build     # tsc -b && vite build
npm run lint
npm run preview
```

**Keep the dev server on port 5173 (or update the backend to match).** The backend's CORS
allowlist (`ALLOWED_ORIGIN` in `Backend/app/config.py`) defaults to exactly
`["http://localhost:5173"]`. If you change the Vite port, the backend config needs a matching
change or every API call will fail CORS.

## TypeScript config notes (`tsconfig.app.json`)

Fairly strict already: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`,
`verbatimModuleSyntax` (type-only imports must use `import type`), `moduleResolution: bundler`.
Keep new code compliant with these rather than loosening them.

## What needs to be built, and the contracts to build against

All shapes below are documented in full (with error cases) in the linked spec file — this is a
pointer, not the full contract.

- **Auth flows** — signup, signin, Google sign-in, guest access, email verification, forgot/reset
  password. Backend is implemented; endpoints and exact request/response shapes are in
  `../specs/01-authentication.md`. Auth is **stateless JWT** (access token, 60 min TTL; refresh
  token, 7 days) — the frontend owns token storage and refresh-on-expiry entirely; there's no
  server session to fall back on.
- **Quota/plan UI** — guest/free/pro tiers with different daily query limits (2/4/40) and a 429
  response when exhausted (`../specs/02-plan-quota-enforcement.md`). The UI should surface remaining
  quota and a clear upgrade path on 429, not just a generic error.
- **File upload** — `.csv`/`.pdf`/`.xlsx` only, plan-based size caps (free ≤3MB, pro ≤10MB), guest
  users blocked entirely. **Backend note:** only the validation middleware exists — there is no
  upload route yet, so this can't be wired end-to-end until the backend adds one
  (`../specs/04-file-upload-ingestion.md`, also flagged in `../Backend/CLAUDE.md`).
- **Chat / query UI** — natural-language question in, structured answer out. **Backend note:** the
  insight pipeline exists but isn't reachable from any route yet — there is no `/chat` endpoint to
  call today (`../specs/06-ai-insight-pipeline.md`).
- **Visual rendering** — the backend's `PipelineOutput.visuals[]` returns one of exactly 9
  `visual_type` values that the frontend must know how to render: `line_chart`, `bar_chart`,
  `pie_chart`, `kpi_card`, `heatmap`, `funnel_chart`, `india_map`, `anomaly_chart`, `ai_summary`.
  Each visual carries `chart_data` (a `{labels, datasets, meta}`-shaped dict — see
  `../specs/06-ai-insight-pipeline.md` for the exact schema) and a `title`. Because
  `visual_type` isn't constrained to an enum on the backend yet, the frontend should defensively
  handle an unrecognized value (e.g. fall back to `ai_summary` or a plain text render) rather than
  crashing.
- **Payment/upgrade UI** — plan upgrade to `pro`. **Design note:** the backend spec
  (`../specs/03-payment-verification.md`) calls for **Razorpay Checkout** (order created
  server-side, `key_id` + `order_id` handed to the frontend to open Razorpay's client-side modal,
  signature returned to the backend for verification). No backend route exists yet for this either
  — see the payment-module note in `../Backend/CLAUDE.md` before building against it.

## Practical guidance

- Nothing has been decided about project structure (folders for `components/`, `hooks/`, `api/`,
  etc.) — establish a convention when you build the first real feature rather than guessing at one
  now, and keep it consistent afterward.
- Since several backend endpoints referenced above don't exist yet, expect to build UI against a
  mocked/stubbed API layer in places — keep that boundary explicit (e.g. a single `api/` module)
  so swapping in the real endpoint later is a one-file change, not a scattered find-and-replace.
- `src/assets/hero.png` already exists in the scaffold (alongside the default Vite/React logos) —
  likely intended for a real landing/hero section; the default logos are template leftovers safe to
  remove once real UI replaces `App.tsx`.
