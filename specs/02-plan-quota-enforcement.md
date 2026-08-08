# Spec 02 — Usage Quota Enforcement

**Status:** 🔶 Redesigned, not yet implemented — replaces the daily-calendar/plan-tier model below
with a rolling-window model. The existing code (`app/middlewares/rate_limiter.py`,
`app/utils/usage.py`) implements the *old* design; this spec now describes the *target* design it
needs to be rewritten to match.
**Source files (need rewriting):** `app/middlewares/rate_limiter.py`, `app/utils/usage.py`,
`app/db/models/user.py`

---

## 1. Problem Statement

The product is staying **free while it validates real usage** (see `03-payment-verification.md` —
payment is paused, not built next). Usage still needs a hard, server-side, race-safe cap — both to
control LLM cost and to create a natural, honest point to say "if you want more than this, tell us."
The model: a Claude-style rolling window (a small number of questions per short window, refreshing
automatically) plus an overall lifetime ceiling.

## 2. Functional Requirements

- **FR1: Rolling window quota** — **4 questions per rolling 6-hour window**, for every user
  (registered or guest — see FR4). The window rolls forward from each user's activity; it is not a
  fixed clock-aligned window, and it is not a UTC-calendar-day reset (that was the old design —
  actively replace it, don't leave both behaviors coexisting).
- **FR2: Lifetime cap** — **100 questions total, ever**, per user. This is cumulative and never
  resets. Once reached, no further questions are answerable regardless of window state — the user
  is routed to the contact flow (FR5).
- **FR3: No plan tiers for now.** Drop the `guest`(2)/`free`(4)/`pro`(40) distinction as an
  *enforcement* concept — everyone gets the same 4-per-6h / 100-lifetime allowance. The `plan`
  column and `plan_checker` middleware can stay in the data model/codebase dormant for a future
  paid tier (see `03`), but nothing should currently branch on `plan` to produce a different quota
  number.
- **FR4: Guest vs. registered enforcement reliability.** Registered accounts enforce both caps
  reliably (tied to `user.id`). Guest accounts are tracked via `device_fingerprint`, same as today
  — this is best-effort for the lifetime cap specifically, since a guest clearing cookies/using a
  new device can reset their apparent history. Documented as a known, accepted limitation (§5), not
  something to solve now.
- **FR5: Cap-reached flow.** When the lifetime cap (FR2) is hit, the response includes a prompt to
  fill out a contact form (name, email, message) rather than a payment upgrade path. Submitting it
  sends an email via the existing `app/services/auth/email_sender.py` async SMTP service — no new
  email infrastructure needed, this reuses what auth already has working. Recipient address is a
  new config value (`CONTACT_FORM_RECIPIENT_EMAIL`), not hardcoded, so it can change without a code
  edit.
- **FR6: Every quota-checked request is still an atomic check-and-increment** — this requirement is
  unchanged from the old design and remains non-negotiable (see Constraints).

## 3. API Contracts

**`rate_limiter(user: User, db: AsyncSession) -> User`** (FastAPI dependency, unchanged shape)
- Error, window exhausted: `429 { "detail": "You've used your 4 questions for this 6-hour window. More unlock at <reset_time>." }`
- Error, lifetime cap reached: `429 { "detail": "You've reached the 100-question limit for now.", "contact_form": true }` — the
  `contact_form` flag is what tells the frontend to render the contact form (FR5) instead of a
  generic "come back later" message; these are different UI states even though both are a 429.

**`POST /contact` (new)** — no auth required beyond having hit the lifetime cap (or accessible
generally; not worth gating further)
- Input: `{ "name": str, "email": str, "message": str }`
- Output 200: `{ "message": "Thanks — we'll be in touch." }`
- Side effect: sends an email to `CONTACT_FORM_RECIPIENT_EMAIL` via the existing async SMTP service.

## 4. Data Model Changes Required

`User` needs two new fields to support the rolling window + lifetime cap (replacing the old
`queries_today` / `last_reset` calendar-day pair, which don't fit a rolling-window model):

| Field | Purpose |
|---|---|
| `questions_in_window` | Count within the current rolling 6h window |
| `window_started_at` | Timestamp the current window began — when `now - window_started_at >= 6h`, the window rolls: reset `questions_in_window` to 0 and set `window_started_at = now` on the next request |
| `questions_lifetime` | Cumulative count, never reset — compared against the `100` cap |

`queries_today` / `last_reset` can be removed once the rewrite lands — they're specific to the old
calendar-day design being replaced, not a general-purpose field worth keeping around unused.

## 5. Constraints

- **Race-safety requirement is unchanged from the old design and must not be relaxed**: the
  check-and-increment for both the window count and the lifetime count needs to happen atomically
  (conditional `UPDATE ... WHERE ... RETURNING`, not separate `SELECT` + `UPDATE`) — same reasoning
  as before, just applied to two counters instead of one.
- **`reset_daily_usage_if_needed` becomes something like `roll_window_if_needed`** — same
  single-source-of-truth principle as before (this logic must live in exactly one place), just a
  different rollover condition (elapsed time since `window_started_at` ≥ 6h, not "has the UTC date
  changed").
- The guest lifetime-cap limitation (FR4) is an accepted tradeoff, not a bug to fix immediately —
  revisit only if guest abuse of the lifetime cap actually becomes a real problem in practice, not
  preemptively.
- Cost containment note: since Claude is used sparingly per query anyway (`12-llm-orchestration.md`
  §3 — roughly 1–2 calls per query, not the whole pipeline), the 100-lifetime/4-per-6h ceiling
  already bounds worst-case per-user cost without needing a separate, more complex cost-based limit.

## 6. Edge Cases & Error Handling

1. **Concurrent requests at the window or lifetime boundary** — same handling as the old design:
   only requests that still fit under the relevant limit at commit time succeed.
2. **A request arrives right as the window is rolling over** — the roll-check and the
   increment need to happen together (or in a way that can't leave `questions_in_window` counted
   against the old window after `window_started_at` has already been updated to the new one).
3. **Guest device-fingerprint collision or reuse across genuinely different people** — same
   pre-existing risk noted in `01-authentication.md`, not new to this spec, but worth remembering
   it also now affects lifetime-cap accuracy, not just quota accuracy.
4. **Contact form submitted with an invalid/fake email** — accept it anyway (no verification loop
   before sending); this is a low-stakes lead-capture form, not an auth flow, and adding
   verification here would just be friction for a form whose only job is "let us know you want
   more."

## 7. Acceptance Criteria

- [ ] A user's 5th question within a rolling 6-hour window returns 429 with a clear reset time; the
      window rolls forward correctly on the next request after 6 hours have elapsed.
- [ ] A user's 101st question ever returns 429 with `contact_form: true`, regardless of window
      state.
- [ ] `POST /contact` sends a real email via the existing SMTP service to
      `CONTACT_FORM_RECIPIENT_EMAIL`.
- [ ] Load test: concurrent requests at either boundary (window or lifetime) never let more through
      than the stated limit.
- [ ] No code path branches on `user.plan` to produce a different quota number.
- [ ] Old calendar-day fields/logic (`queries_today`, `last_reset`,
      `reset_daily_usage_if_needed`) are fully removed, not left dead alongside the new fields.
