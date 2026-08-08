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

## Daily-quota reset (`app/utils/usage.py::reset_daily_usage_if_needed`)

**Rule:** this is the only function that may decide "has this user's daily quota rolled over."

**Why:** this logic used to be duplicated — a rolling 24-hour window in `guest_auth.py` vs. a
UTC-calendar-day check in `rate_limiter.py` — and the two could disagree about whether a given user
was still within quota. It also used to mix naive and timezone-aware datetimes, which raises
`can't subtract offset-naive and offset-aware datetimes` under real conditions. The consolidated
version always compares in UTC using timezone-aware datetimes and is the single source of truth.
Adding a third copy of this logic anywhere reintroduces the original bug class.

## Rate limiter's atomic update (`app/middlewares/rate_limiter.py`)

**Rule:** the quota check-and-increment must stay a single statement:
```sql
UPDATE users SET queries_today = queries_today + 1
WHERE id = :id AND queries_today < :limit
RETURNING queries_today
```

**Why:** a "read `queries_today`, check it, then `UPDATE`" implementation as two separate statements
is a race condition — two concurrent requests can both read "under limit" before either commits,
letting both through and exceeding the quota. Folding the check into the `UPDATE`'s `WHERE` clause
makes Postgres serialize it at the row level: only requests that still fit under the limit *at the
moment of the write* succeed. This is the entire reason the limiter looks like one dense query
instead of three readable lines — don't "clean it up" back into separate statements.

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

**Rule:** an unrecognized `user.plan` value falls back to the `free` quota in the rate limiter and
to guest-level (0) in the plan hierarchy — both via `.get(value, default)`.

**Why:** this fails safe (a corrupted or future-tier plan value can't accidentally grant more access
than intended) but currently does so **silently** — no warning is logged. If you touch this code,
add the missing log line so bad data gets caught rather than masked; don't just rely on the
fallback being "safe enough" to ignore.
