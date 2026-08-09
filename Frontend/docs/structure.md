# Project structure & practical guidance

Open this when deciding how to organize the codebase or how to bridge mocked vs. real API calls —
not needed for day-to-day feature work once these decisions are made once.

## Suggested structure

Nothing is decided yet — no `components/`, `hooks/`, `api/`, `pages/`, or `types/` directories
exist. Establish a convention on the first real feature, then stay consistent. A reasonable shape
given how API-heavy this app is:

```
src/
  api/            one module per backend domain (auth.ts, files.ts, chat.ts, payments.ts) —
                  the seam that lets a mocked endpoint become a real one later, see below
  types/           TypeScript interfaces mirroring docs/type-contracts.md
  components/      shared, reusable UI (buttons, form fields, one chart wrapper per visual_type)
  features/        one folder per screen/flow: auth/, chat/, upload/, payments/, dashboard/
  hooks/           e.g. useAuth, useQuota, useTokenRefresh
```

## Keep the mock/real API boundary explicit

Several backend endpoints referenced in `docs/type-contracts.md` don't exist yet (upload, chat,
payments). Keep the mock behind one module per domain (`api/chat.ts`, etc.) so swapping a mock for
the real endpoint later is a one-file change, not a scattered find-and-replace. Components should
call `api/chat.ts`'s `sendQuery()` and not know or care whether it's currently mocked.

## Practical guidance

- **Cold starts:** the backend targets Render's free tier — expect ~30–40s cold starts. A bare
  spinner on first load will feel broken; design loading states with that explicitly in mind.
- **Generic auth errors:** the backend deliberately returns the same message for "no such user" and
  "wrong password" (anti-enumeration). Display it as-is — don't try to infer or re-word a more
  specific client-side message, that defeats the design.
- **429 vs. 429:** the two quota states (specs/02 FR5, specs/14 §5.6) are both a 429 status code but
  mean different things. Window exhausted (`contact_form` absent, resets after the rolling 6h window)
  is an inline "come back later" notice with the reset time. Lifetime cap (`contact_form: true`, never
  resets) is a distinct, more permanent-feeling card with the contact form inline. The UI must branch
  on the `contact_form` flag in the 429 body — don't guess from the status code alone. There is no
  upgrade messaging in MVP (payments paused, `specs/03`).

## What's already in the scaffold

- `src/assets/hero.png` — a custom asset already added (alongside the default Vite/React logos),
  likely intended for a real landing/hero section.
- `public/icons.svg`, `public/favicon.svg` — present, referenced from `index.html`/`App.tsx`.
- `src/App.tsx`, `src/App.css` — pure template boilerplate (counter, Vite/React links). Safe to
  fully replace; nothing in the current `App.tsx` is product logic worth preserving.
