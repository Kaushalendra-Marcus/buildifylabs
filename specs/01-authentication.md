# Spec 01 — Authentication

**Status:** ✅ Implemented (with known gaps below)
**Source files:** `app/routes/auth.py`, `app/schemas/auth.py`, `app/schemas/user.py`,
`app/services/auth/*.py`, `app/db/models/user.py`, `app/middlewares/auth_middleware.py`

---

## 1. Problem Statement

Users need multiple low-friction ways into the product — try it as a guest, sign up with
email, or sign in with Google — while the system reliably recognizes returning guests (to stop
quota abuse), confirms real ownership of an email before granting persistent access, and supports
secure credential recovery. All of this runs on stateless JWTs, since the backend has no
server-side session store.

## 2. Functional Requirements

- **FR1 — Register:** Email + password (+ optional name) → account created, verification email
  sent, access/refresh tokens issued immediately (registration does **not** block on verification —
  only subsequent logins do).
- **FR2 — Login:** Email + password → rejected with a single generic error for both "no such user"
  and "wrong password" (anti-enumeration); additionally rejected if unverified or disabled.
- **FR3 — Guest access:** Client sends a `device_id`. If a user with that `device_fingerprint`
  exists, reuse it (after checking/rolling over its daily quota); otherwise create a new guest user.
- **FR4 — Google OAuth:** Client sends a Google ID token. Verified server-side against
  `GOOGLE_CLIENT_ID`. If an email/password account with the same email already exists, it is
  upgraded in place (linked to `google_id`, `auth_provider` becomes `"google"`, `is_verified` is
  forced `True`). Otherwise a new user is created.
- **FR5 — Token refresh:** Refresh token → new access + refresh pair, rejecting wrong token type,
  unknown user, or disabled user.
- **FR6 — Email verification:** Time-limited (20 min) signed link sets `is_verified = True`.
- **FR7 — Forgot password:** Always returns the same generic message regardless of whether the
  email exists; only email/password accounts (`auth_provider == "email"`) actually receive a reset
  email.
- **FR8 — Reset password:** Time-limited (20 min) signed token + new password (min 8 chars) →
  updates `hashed_password`.

## 3. API Contracts

All responses on success use `AuthResponse` unless noted:
```json
{
  "user": { "id": "uuid", "email": "string|null", "name": "string|null", "plan": "guest|free|pro" },
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer"
}
```

| Method & Path | Input Schema | Success | Error cases |
|---|---|---|---|
| `POST /auth/signup` | `UserCreate { email, name?, password? }` | 200 `AuthResponse` | 400 user already exists; 400 validation |
| `POST /auth/signin` | `LoginRequest { email, password }` | 200 `AuthResponse` | 400 invalid credentials / not verified / disabled |
| `POST /auth/guest` | `GuestAuthRequest { device_id? }` | 200 `AuthResponse` | 403 guest quota exhausted |
| `POST /auth/google` | `GoogleAuthRequest { token }` | 200 `AuthResponse` | 401 invalid Google token; 400 no email from Google; 403 disabled |
| `POST /auth/refresh` | `RefreshTokenRequest { refresh_token }` | 200 `TokenResponse` | 401 invalid/wrong-type/expired; 404 not found; 403 disabled |
| `GET /auth/verify-email?token=` | query param | 200 `{ "message": string }` | 400 invalid/expired; 404 not found |
| `POST /auth/forgot-password` | `ForgotPasswordRequest { email }` | 200 `{ "message": string }` (always same) | — |
| `POST /auth/reset-password` | `ResetPasswordRequest { token, new_password }` | 200 `{ "message": string }` | 400 invalid/expired token; 404 not found |

`TokenResponse`: `{ access_token, refresh_token, token_type }` (no `user` object).

## 4. Constraints

- Access token TTL = 60 min, refresh TTL = 7 days (`config.py`).
- Verification and password-reset tokens are JWTs signed with the **same** `JWT_SECRET` as
  access/refresh tokens, distinguished only by a `type` claim (`"verify"` / `"reset_password"`) —
  not a separate secret.
- Passwords hashed with bcrypt via `passlib`; minimum 8 characters enforced at the schema layer.
- `hashed_password` is nullable at the DB level to support Google/guest accounts that never set one.
- Login and forgot-password both use deliberately generic error messages to prevent user
  enumeration.

## 5. Edge Cases & Error Handling

1. **[Gap] `UserCreate.password` is `Optional[str] = None`**, but `register_user()` unconditionally
   calls `hash_password(password)`. An omitted password on signup will raise inside `passlib`
   rather than surfacing a clean validation error. **Fix:** make `password` required for the
   email/password signup path.
2. **[Gap] Guest lookup can raise `MultipleResultsFound`.** If `device_id` is omitted, the guest
   lookup filters on `device_fingerprint == None`, which is a Postgres `IS NULL` match — and since a
   unique constraint treats multiple `NULL`s as distinct, more than one such row can legally exist.
   `scalar_one_or_none()` throws if more than one row matches. **Fix:** require `device_id`
   non-null in `GuestAuthRequest`, or switch the query to `.first()`.
3. **Guest identity is fully client-trusted.** There is no server-side fingerprint (hash of
   IP + User-Agent) — a user can get a fresh guest quota just by sending a new random `device_id`.
   Accepted as an MVP limitation; revisit before public launch.
4. **No resend-verification-email endpoint.** A user stuck unverified (lost the email, link
   expired) currently has no self-serve path back in.
5. **Google-linked account attempting email/password login** (`hashed_password is None`) is safely
   handled — `login_user` short-circuits on `not user.hashed_password` before calling
   `verify_password`, so this does not crash.
6. **Password reset tokens are time-limited but not single-use.** Nothing invalidates a reset token
   after it's been used once; a leaked link remains valid for the rest of its 20-minute window even
   after a successful reset.
7. **No rate limiting on `/auth/signin` or `/auth/verify-email`.** `config.py` defines
   `LOGIN_RATE_LIMIT` and `VERIFY_EMAIL_RATE_LIMIT` but neither is wired into any route or
   middleware yet — both endpoints are currently brute-forceable.

## 6. Acceptance Criteria

- [ ] All 8 endpoints return the documented status codes on both happy and unhappy paths.
- [ ] A repeated guest `device_id` reliably reuses the same user row and reflects correct
      remaining quota.
- [ ] An unverified user cannot sign in even with the correct password.
- [ ] Gap #1 and #2 above are fixed and covered by tests.
- [ ] `/auth/signin` and `/auth/verify-email` are rate-limited per the config values already defined.
