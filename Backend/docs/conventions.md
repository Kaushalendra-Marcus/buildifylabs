# Conventions — why the "hard invariants" exist

Only open this file when you're actually about to touch one of the pieces below, or when you want
to understand why something that looks over-engineered is written the way it is. Each entry: the
rule, then the failure it prevents.

## Model registry (`app/db/models/__init__.py`)

**Rule:** every SQLAlchemy model must be imported here.

**Why:** `User` declares relationships to `FileUpload`, `Payment`, and `QueryLogs` by string name
(`relationship("FileUpload")`, etc). SQLAlchemy only resolves those strings against classes that
have actually been imported somewhere by the time mappers are configured — which happens
automatically the first time *any* query touches `User`. Nothing in the normal import path
(routes → services → `User`) imports the sibling model files on its own. Skip the import here and
the very first signup/login call crashes with `InvalidRequestError: failed to locate a name
('FileUpload')` — a failure in a completely unrelated request, triggered by a model you added
somewhere else entirely.

## Quota window rollover (`app/utils/usage.py`)

**Rule:** the "has this user's 6h rolling window rolled over" rule lives in exactly one place:
`usage.py`'s `QUOTA_WINDOW` constant and `window_elapsed_clause()` (the SQLAlchemy form the rate
limiter folds into its atomic `UPDATE`), with `window_reset_at()` for the 429 reset-time message.

**Why:** the old `reset_daily_usage_if_needed` used to be duplicated — a rolling 24-hour window in
`guest_auth.py` vs. a UTC-calendar-day check in `rate_limiter.py` — and the two could disagree about
whether a given user was still within quota. Adding a third copy of this logic anywhere reintroduces
the original bug class (and a `date()`-based rule even mixes naive/timezone-aware datetimes, which
raises `can't subtract offset-naive and offset-aware datetimes`). The rewrite keeps the one rule in
`usage.py` and makes the rate limiter consume it.

## Rate limiter's atomic update (`app/middlewares/rate_limiter.py`)

**Rule:** the quota check-and-increment must stay a single statement, now over **both** counters
plus the roll condition:

```sql
UPDATE users
SET questions_in_window = CASE WHEN (window rolled) THEN 1 ELSE questions_in_window + 1 END,
    window_started_at    = CASE WHEN (window rolled) THEN now ELSE window_started_at END,
    questions_lifetime   = questions_lifetime + 1
WHERE id = :id
  AND questions_lifetime < 100
  AND (window rolled OR questions_in_window < 4)
RETURNING questions_in_window, questions_lifetime, window_started_at
```

**Why:** a "read the counters, check both limits, then `UPDATE`" implementation as separate
statements is a race condition — two concurrent requests can both read "under limit" before either
commits, letting both through and exceeding the quota. Folding the checks into the `UPDATE`'s
`WHERE` clause makes Postgres serialize it at the row level: only requests that still fit under the
limit *at the moment of the write* succeed. Folding the **window roll** into the same statement makes
a request arriving right as the window rolls take the fresh-window path instead of counting against
the old window — there is no instant where `window_started_at` has advanced but the count hasn't been
reset. This is the entire reason the limiter looks like one dense query instead of three readable
lines — don't "clean it up" back into separate statements.

## JWT token types share one secret (`app/services/auth/token_service.py`, `email_verification.py`)

**Rule:** access, refresh, email-verification, and password-reset tokens are all signed with the
same `JWT_SECRET`, distinguished only by a `type` claim (`"access"`, `"refresh"`, `"verify"`,
`"reset_password"`).

**Why:** this is a deliberate simplicity choice, not an oversight — one secret to rotate, one
decode path (`decode_token`) shared by every consumer, with each caller checking `payload["type"]`
before trusting the token for its purpose. A new token kind should follow the same pattern (new
`type` value, same secret) rather than introducing a second secret to manage.

## Generic auth error messages (`app/services/auth/email_auth.py`, `app/routes/auth.py`)

**Rule:** login failure returns the same message ("Invalid email or password") whether the account
doesn't exist or the password is wrong. Forgot-password always returns the same message
("If that email is registered...") regardless of whether the email is registered.

**Why:** anti-enumeration. A more specific error ("no account with that email") lets an attacker
build a list of valid registered emails by brute-forcing the endpoint. This is a security property,
not a UX oversight — don't make these messages more specific for debugging convenience without
thinking through the enumeration risk first.

## SQL safety centralization (`app/middlewares/sql_sanitizer.py`)

**Rule:** all SQL safety validation lives in `sanitize_sql()`. `sql_generator.py`'s
`clean_sql_response()` is text-cleanup only (stripping markdown fences/prose) and must never
duplicate or second-guess a safety decision.

**Why:** `sanitize_sql()` used to be (conceptually, per the design history) a regex blacklist over
uppercased query text — which can't tell a forbidden keyword inside a string literal from a real
one, doesn't understand statements nested inside CTEs/subqueries, and is trivially bypassed by any
SQL syntax the author didn't think to test. Parsing into a real `sqlglot` AST and walking the full
tree (not just the top level) closes all of that at once: a `DROP TABLE` smuggled inside a CTE is
caught the same way a top-level one is. If a second safety check gets added elsewhere (e.g. "quick
regex check before calling the generator"), it reintroduces exactly the class of bypass this design
was built to eliminate.

## Plan/quota values failing toward the restrictive side (`rate_limiter.py`, `plan_checker.py`)

**Rule:** an unrecognized `user.plan` value falls back to the restrictive side — the rate limiter
doesn't branch on `plan` for a quota number at all anymore (everyone gets 4-per-6h / 100-lifetime,
per `../specs/02` §2 FR3), and `plan_checker` falls back to guest-level (0) for an unknown tier.

**Why:** this fails safe (a corrupted or future-tier plan value can't accidentally grant more access
than intended). `plan_checker` now **logs a warning** on an unrecognized plan value so bad data gets
caught rather than masked; don't rely on the "safe enough" fallback alone.
