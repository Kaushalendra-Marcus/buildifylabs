# Spec 03 — Payment: Razorpay Integration

**Status:** ❌ Not implemented — `Payment` model/schema exist but need revision (see below); no
route or service layer written yet.
**Source files (existing, need revision):** `app/db/models/payment.py`, `app/schemas/payment.py`
**Source files (missing):** `app/routes/payment.py`, `app/services/payment/*`

> **Revision note:** This supersedes the earlier manual UPI + UTR design. That approach was chosen
> to avoid gateway fees/integration cost; the decision has been reversed in favor of **Razorpay**
> for reliability (automated capture + webhook verification instead of manual admin review) and
> lower operational overhead. The `utr`-based fields below are being replaced, not extended.

---

## 1. Problem Statement

Upgrading a user from `free` to `pro` needs to happen reliably and automatically the moment a real
payment clears — without a human manually checking a UTR number. Razorpay handles the actual
money movement (UPI/card/netbanking); this module's job is to create the order, verify that a
completed payment is genuine (not spoofed by the client), and upgrade the account exactly once.

## 2. Functional Requirements

- **FR1:** Authenticated, non-guest user can request a Razorpay order for the upgrade amount.
- **FR2:** Frontend opens Razorpay Checkout using the returned `order_id`; on completion, Razorpay
  gives the frontend `razorpay_payment_id`, `razorpay_order_id`, `razorpay_signature`.
- **FR3:** Backend verifies that signature (HMAC-SHA256, using the Razorpay key secret) before
  trusting the client's "I paid" claim — this is a UX-speed optimization, **not** the source of
  truth.
- **FR4:** Razorpay webhooks (`payment.captured`, `payment.failed`) are the **source of truth**.
  The webhook handler verifies the `X-Razorpay-Signature` header, then updates payment status and
  upgrades the user's plan on a captured event.
- **FR5:** The plan upgrade must happen **exactly once** even if the webhook is delivered more than
  once (Razorpay retries webhook delivery on non-2xx responses).
- **FR6:** A user can view their own payment/order history.

## 3. API Contracts

**`POST /payments/create-order`** (auth required, non-guest)
- Input: `{}` (amount is currently fixed; see Constraints) — reserved for `{ "plan": "pro" }` if
  multiple tiers are added later.
- Output 201:
  ```json
  {
    "order_id": "order_xxx",
    "amount": 29900,
    "currency": "INR",
    "key_id": "rzp_xxx"
  }
  ```
  `amount` is in **paise** (smallest currency unit) — see Constraints. `key_id` is the Razorpay
  **public** key, safe to expose to the frontend for Checkout initialization.
- Side effect: creates a `Payment` row with `status = "created"`, `razorpay_order_id` set,
  `user_id` set to the caller.
- Errors: `403` guest user.

**`POST /payments/verify`** (auth required) — client-side callback, UX-speed path
- Input:
  ```json
  {
    "razorpay_order_id": "order_xxx",
    "razorpay_payment_id": "pay_xxx",
    "razorpay_signature": "string"
  }
  ```
- Output 200: `PaymentResponse` with `status = "paid"` if the signature is valid **and** the order
  belongs to the calling user.
- Errors: `400` signature mismatch; `404` order not found; `403` order belongs to a different user.

**`POST /payments/webhook`** (**no auth** — public endpoint, trust established via signature only)
- Input: raw Razorpay webhook JSON body + `X-Razorpay-Signature` header.
- Output: `200` (must always ack quickly, even on business-logic no-ops, so Razorpay doesn't
  endlessly retry) or `400` if the signature itself is invalid.
- On `payment.captured`: find `Payment` by `razorpay_order_id`, set `status = "paid"`,
  `razorpay_payment_id`, `verified_at`; upgrade `user.plan = "pro"` — **idempotent**, see Edge
  Cases.
- On `payment.failed`: set `status = "failed"`.

**`GET /payments/me`** (auth required) → `PaymentResponse[]`
```json
{
  "id": "uuid", "razorpay_order_id": "order_xxx", "razorpay_payment_id": "pay_xxx|null",
  "amount": 299.00, "status": "created|paid|failed", "created_at": "iso8601",
  "verified_at": "iso8601|null"
}
```

## 4. Data Shape Changes Required

`Payment` model needs revision from the UTR design:

| Field | Change |
|---|---|
| `utr` | **Remove.** Razorpay doesn't use UPI UTR numbers directly at the app level. |
| `razorpay_order_id` | **Add** — `String`, unique, indexed, set at order creation. |
| `razorpay_payment_id` | **Add** — `String`, nullable, set once captured. |
| `razorpay_signature` | **Add** (optional to persist — useful for audit, not required for logic since it's verified once, not re-checked later). |
| `status` | Keep as `String`, but values become `"created" \| "paid" \| "failed"` (was `"pending"/"verified"/"rejected"`). |
| `admin_note` | Keep — still useful for support/refund notes, no longer required for the core flow. |

## 5. Constraints

- New config required: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`.
  `UPI_ID` config value is no longer used and can be removed once this ships.
- **Razorpay amounts are in the smallest currency unit** (paise for INR) — `PAYMENT_AMOUNT = 299`
  (rupees, current config) must be converted to `29900` paise when creating the order. This is a
  classic source of off-by-100 bugs; keep the conversion in exactly one place.
- The webhook endpoint **must be publicly reachable with no auth middleware**, but must **never**
  trust the payload without first verifying `X-Razorpay-Signature` against `RAZORPAY_WEBHOOK_SECRET`
  — this is the entire security boundary for this endpoint.
- Client-side `/payments/verify` is a convenience path for immediate UI feedback. The webhook must
  remain authoritative — a user who closes the browser right after paying (before the client-side
  callback fires) must still get upgraded once the webhook arrives.
- The admin-role gap noted in earlier drafts of this spec is **no longer blocking** for the core
  upgrade flow (Razorpay + webhook automates it). An admin surface is still useful later for
  refunds/support/manual overrides, but is not a prerequisite to ship this module.

## 6. Edge Cases & Error Handling

1. **Duplicate webhook delivery** for the same `payment.captured` event (Razorpay retries on
   non-2xx or timeout) → handler must check current `status` before acting; if already `"paid"`,
   return `200` immediately without re-running the plan upgrade.
2. **Tampered/forged webhook payload** (wrong or missing signature) → reject with `400`, log as a
   potential attack, never process the payload.
3. **Client sends a valid signature for an `order_id` that exists but belongs to a different
   user** → must be rejected (`403`), not silently trusted just because the signature checks out
   cryptographically — signature proves the *payment* is real, not that *this caller* owns it.
4. **Order created but payment abandoned** (user closes Checkout) → `Payment` row sits in
   `"created"` indefinitely; Razorpay orders don't auto-expire. Needs a periodic cleanup job or a
   TTL-based UI treatment (e.g. hide/retry after N hours) — not yet designed in detail.
5. **`payment.failed` webhook arrives after a `payment.captured` was already processed** (out-of-
   order delivery) → must not downgrade a already-`"paid"` record; failed events should only affect
   rows still in `"created"`.
6. **Amount tampering concern:** always verify against the amount stored on the `Payment` row at
   order-creation time, not a "current" price constant, so a mid-flight price change can't create
   inconsistent state for in-flight orders.
7. **Refunds** (Razorpay Refund API) are out of scope for this spec but should downgrade the user's
   plan when implemented — flagged as a follow-up, not required for initial ship.

## 7. Acceptance Criteria

- [ ] `POST /payments/create-order` returns a real Razorpay order and a `Payment` row in
      `"created"` state.
- [ ] A successful payment (via webhook, treated as authoritative) upgrades `user.plan` to `"pro"`
      exactly once, verified under duplicate webhook delivery.
- [ ] A forged webhook (bad signature) is rejected and never reaches the plan-upgrade logic.
- [ ] `/payments/verify` rejects a signature/order pair that doesn't belong to the calling user.
- [ ] Amounts are correct in paise throughout order creation, verification, and webhook handling —
      no off-by-100 errors.
