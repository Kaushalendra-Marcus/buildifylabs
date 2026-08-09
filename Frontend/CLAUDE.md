# CLAUDE.md — Frontend

This file loads into context every session — keep it short. Full data contracts and structural
guidance live in `docs/`; open only the section relevant to what you're building right now. **Don't
read all of `docs/` or `specs/` up front** — use §2's table.

## 1. Orientation

Frontend for **BuildifyLabs** (see `../Backend/CLAUDE.md` for the product description). **F0
foundations are in** — structure (`src/{api,types,lib/schemas,components,features,hooks}`), design
tokens, the API seam, and the frozen type contract (`src/lib/schemas/visuals.ts`) exist. **F1 auth
screens are in** — signup / signin / Google (GIS) / guest (`device_id`) / verify-email /
forgot- / reset-password, wired to the live backend with route guards (`src/App.tsx` routes them to
`/app`, guarded). **F2 Chat Workspace shell is in** — the `/app` surface is now the real shell
(`src/features/chat/`, specs/14 §3): 56px header (logo, quota chip, plan badge, account menu, new
chat), 0/280px chat-history rail (collapsed by default <768px, **overlay** on narrow viewports),
message stream, and a composer pinned to the foot of the stream column — no resizable panel, no
fullscreen affordance. **F3 message stream components are in** — the `MessageStream` region now
renders the four `specs/14` §4 message types from the Zustand `chat-store` (`src/features/chat/`):
user bubble (file chip above), assistant normal answer (prose → `repeat(auto-fit, minmax(240px,
1fr))` visual cards grid with graph/table spanning 2 cols → hedged "Possible factors" insights strip
collapsed by default → **always-visible trust footer**: show-the-query / confidence meter (gated on
0..1) / flag-this-answer wired to `POST /chat/flag`), clarification quick-pick with tap-sends-verbatim
(`chat-store.addUserMessage`), and the neutral fallback notice — the `data-visual-type` + wide card
classes are the seams F4's visual lookup consumes. **F4 seven visual components are in** —
`src/components/visuals/` (`MetricCard`, `GraphCard` [Recharts line/bar/pie/area], `BusinessSummaryTable`,
`ComparisonCard`, `InsightCard`, `AlertList`, `StatusBadge`) built to `visuals.ts` props; `VisualCard` is
the **plain type→component lookup** the F3 grid seam calls, with `UnknownVisualCard` as the defensive
fallback for an unrecognized `visual_type`. Auth, plan/quota, upload, and chat (`POST /chat`) have a live backend to build
against today (§3); payment needs mocked data behind a clean API seam until its route ships.

## 2. What to read, by task

| Task | Read this — nothing else |
|---|---|
| Any task, first | This file only |
| Auth screens (signup/signin/Google/guest/verify/reset) | `../specs/01-authentication.md` |
| Quota/plan display, 429 handling | `../specs/02-plan-quota-enforcement.md` |
| Payment/upgrade flow | `../specs/03-payment-verification.md` + `docs/type-contracts.md` §Payment |
| File upload flow | `../specs/04-file-upload-ingestion.md` + `docs/type-contracts.md` §Upload |
| Chat UI + visual/chart rendering | `../specs/06-ai-insight-pipeline.md` + `docs/type-contracts.md` §Chat |
| Chat Workspace page layout / visual design | `../specs/14-chat-workspace-ui-design.md` |
| Need a TypeScript type for an API response | `docs/type-contracts.md` — don't re-derive from the Python schemas each time |
| Deciding project structure, or the mock-vs-real API boundary | `docs/structure.md` |
| Full architecture / module status | `../specs/00-overview.md` |
| Product strategy — positioning, integrations, WhatsApp, retention | `../specs/09-differentiation-and-gtm.md` |
| Trust UX for AI answers (show-the-query, confidence, flagging) | `../specs/10-trust-safety-compliance.md` §2 |

## 3. What's buildable today vs. mocked

| Feature | Backend status | Approach |
|---|---|---|
| Auth (signup/signin/Google/guest/verify/reset) | ✅ Live | Build against the real API |
| Quota display, 429 handling | ✅ Live | Build against the real API |
| File upload | ✅ Live (upload + list + status) | Build against the real API |
| Chat + charts | ✅ Live (`POST /chat` + `/chat/flag`) | Build against the real API; `api/chat.ts` seam |
| Payment / upgrade | ❌ Not implemented | Mock `api/payments.ts`; swap in later |

## 4. Tech stack & scripts

React 19 + TypeScript + Vite 8 + ESLint 9. Deliberate stack choices made once in F0 and held
consistent: **react-router** · **Zustand** (stores in `features/*`) · a thin **fetch wrapper**
(`src/lib/http.ts`) · **plain CSS with custom-property design tokens** (no framework; `index.css`) ·
**Recharts** (charts) · **lucide-react** (icons) · **Google Identity Services** (sign-in, script in
`index.html`) · **Vitest + React Testing Library** (tests).

```bash
npm install && npm run dev     # http://localhost:5173 — keep this port, backend CORS is locked to it
npm run build / npm run lint / npm test / npm run preview
```

## 5. Environment variables

`VITE_API_BASE_URL` (defaults to `http://localhost:8000`), `VITE_GOOGLE_CLIENT_ID` (must match the
backend's `GOOGLE_CLIENT_ID`), `VITE_RAZORPAY_KEY_ID` (public key only, once payments are wired).
Vite only exposes `VITE_`-prefixed vars to client code — never put a real secret in any frontend
`.env`. Token storage: access token in memory only, refresh token in localStorage
(`src/lib/token-storage.ts`).

## 6. Working with `specs/`

`../specs/` (shared with the backend) is the authoritative product spec — there's no frontend-only
spec; build against the API contracts documented there. Format: Problem Statement → Functional
Requirements → API Contracts → Constraints → Edge Cases → Acceptance Criteria. Open only the file
for the feature you're building (§2).
