/** Payment contracts — mirror of docs/type-contracts.md §Payment. Designed,
 *  not implemented (specs/03 paused) — the frontend calls mocked `api/payments.ts`
 *  behind these shapes until the real Razorpay routes ship (F7). */

export interface CreateOrderResponse {
  order_id: string;
  amount: number; // paise, not rupees — ₹299 = 29900
  currency: 'INR';
  key_id: string; // Razorpay public key, safe to expose client-side
}

export interface PaymentResponse {
  id: string;
  razorpay_order_id: string;
  razorpay_payment_id: string | null;
  amount: number; // rupees (decimal) here, unlike the paise value above
  status: 'created' | 'paid' | 'failed';
  created_at: string;
  verified_at: string | null;
}
