# Spec 02 — Plan Tiers & Quota Enforcement

**Status:** ✅ Implemented
**Source files:** `app/middlewares/rate_limiter.py`, `app/middlewares/plan_checker.py`,
`app/utils/usage.py`, `app/db/models/user.py`

---

## 1. Problem Statement

Free LLM inference and free-tier infra budgets require hard per-user daily caps, enforced
server-side, safe under concurrent requests — without a Redis-backed counter (the current
implementation keeps the counter directly on the `users` row in Postgres).

## 2. Functional Requirements

- **FR1:** Three tiers with distinct daily query quotas: `guest` = 2, `free` = 4, `pro` = 40.
- **FR2:** Quota resets on UTC **calendar-day** rollover, not a rolling 24h window.
- **FR3:** Every quota-checked request performs an atomic check-and-increment — never a
  separate read-then-write.
- **FR4:** Feature gating (`plan_checker`) is independent of quota (`rate_limiter`) — a route can
  require a minimum plan tier regardless of remaining quota for that request.
- **FR5:** Plan hierarchy for gating purposes: `guest`(0) < `free`(1) < `pro`(2).

## 3. API Contracts

These are FastAPI dependencies, not HTTP endpoints — consumed via `Depends(...)` on other routes.

**`rate_limiter(user: User, db: AsyncSession) -> User`**
- Input: current authenticated user (from `get_current_user`), DB session.
- Output: the same `User`, with `queries_today` reflecting the post-increment value.
- Error: `429 { "detail": "Daily limit reached. Upgrade your plan." }` when quota is exhausted.

**`plan_checker(required_plan: str) -> Callable`**
- A dependency **factory** — call with the required tier name, use the returned callable as a
  dependency, e.g. `Depends(plan_checker("pro"))`.
- Output: the current `User` if their plan meets or exceeds `required_plan`.
- Error: `403 { "detail": "{required_plan} plan required" }`.

## 4. Constraints

- Race-safety is achieved with a single statement:
  `UPDATE users SET queries_today = queries_today + 1 WHERE id = :id AND queries_today < :limit
  RETURNING queries_today`, relying on Postgres row-level locking. **Must not be refactored back
  into separate `SELECT` + `UPDATE` calls** — that reintroduces the race condition this replaced.
- `reset_daily_usage_if_needed()` in `app/utils/usage.py` is the **single source of truth** for
  rollover logic. It replaced two previously-divergent implementations (a rolling-24h check in
  guest auth vs. a calendar-day check in the rate limiter) that could disagree. Do not duplicate
  this logic elsewhere.
- The guest quota value (`2`) is currently defined **twice** — `GUEST_DAILY_LIMIT` in
  `guest_auth.py` and `LIMITS["guest"]` in `rate_limiter.py`. They agree today but are not
  centralized; a future change to one without the other will silently desync them.

## 5. Edge Cases & Error Handling

1. **Concurrent requests at the quota boundary:** only requests that still fit under the limit at
   commit time succeed; the rest correctly receive 429 (by design of the conditional `UPDATE`).
2. **Unexpected `user.plan` value** (corrupted data or a future tier not yet in `LIMITS`/`hierarchy`):
   `rate_limiter` falls back to the `free` quota via `.get(user.plan, LIMITS["free"])`;
   `plan_checker` falls back to level `0` (guest) via `.get(user.plan, 0)`. Both fail toward the
   safer/more restrictive side, but silently — no logging on an unrecognized plan value. Should add
   a warning log so bad data is caught rather than masked.
3. **Double DB hit per request:** if a route depends on both `rate_limiter` and `plan_checker`,
   `get_current_user` (and its DB query) runs twice, since each depends on it independently. Minor
   inefficiency, not a correctness issue — worth a `Depends` cache note if it becomes a hot path.

## 6. Acceptance Criteria

- [ ] Load test: N concurrent requests exactly at the limit boundary never let more than `limit`
      requests through in a single UTC day.
- [ ] Guest, free, and pro users each get 429 on exactly their (limit + 1)th request of the day,
      and are unblocked immediately after UTC midnight.
- [ ] `GUEST_DAILY_LIMIT` is sourced from one place only (dedup fix).
- [ ] An unrecognized `plan` value is logged, not just silently downgraded.
