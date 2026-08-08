# CLAUDE.md — Frontend

This file loads into context every session — keep it short. Full data contracts and structural
guidance live in `docs/`; open only the section relevant to what you're building right now. **Don't
read all of `docs/` or `specs/` up front** — use §2's table.

## 1. Orientation

Frontend for **BuildifyLabs** (see `../Backend/CLAUDE.md` for the product description). **Still the
unmodified Vite/React/TS scaffold** — `src/App.tsx` is template boilerplate, nothing product-
specific exists yet. Only auth and plan/quota have a live backend to build against today (§3);
chat, upload, and payment need mocked data behind a clean API seam until their routes ship.

## 2. What to read, by task

| Task | Read this — nothing else |
|---|---|
| Any task, first | This file only |
| Auth screens (signup/signin/Google/guest/verify/reset) | `../specs/01-authentication.md` |
| Quota/plan display, 429 handling | `../specs/02-plan-quota-enforcement.md` |
| Payment/upgrade flow | `../specs/03-payment-verification.md` + `docs/type-contracts.md` §Payment |
| File upload flow | `../specs/04-file-upload-ingestion.md` + `docs/type-contracts.md` §Upload |
| Chat UI + visual/chart rendering | `../specs/06-ai-insight-pipeline.md` + `docs/type-contracts.md` §Chat |
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
| File upload | ⚠️ Validator only, no route | Mock `api/files.ts`; swap in later |
| Chat + charts | ⚠️ Pipeline built, no route | Mock `api/chat.ts`; swap in later |
| Payment / upgrade | ❌ Not implemented | Mock `api/payments.ts`; swap in later |

## 4. Tech stack & scripts

React 19 + TypeScript + Vite 8 + ESLint 9. Nothing else chosen yet (router, state, CSS, HTTP
client, charts) — pick deliberately per screen, then stay consistent across the app.

```bash
npm install && npm run dev     # http://localhost:5173 — keep this port, backend CORS is locked to it
npm run build / npm run lint / npm run preview
```

## 5. Environment variables

None exist yet. When wiring real API calls, you'll need: `VITE_API_BASE_URL`,
`VITE_GOOGLE_CLIENT_ID` (must match the backend's `GOOGLE_CLIENT_ID`), `VITE_RAZORPAY_KEY_ID`
(public key only, once payments are wired). Vite only exposes `VITE_`-prefixed vars to client code
— never put a real secret in any frontend `.env`.

## 6. Working with `specs/`

`../specs/` (shared with the backend) is the authoritative product spec — there's no frontend-only
spec; build against the API contracts documented there. Format: Problem Statement → Functional
Requirements → API Contracts → Constraints → Edge Cases → Acceptance Criteria. Open only the file
for the feature you're building (§2).
