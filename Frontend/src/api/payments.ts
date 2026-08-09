/**
 * Payment API — MOCKED (specs/03 paused, post-checkpoint). Shape matches
 * docs/type-contracts.md §Payment exactly, so wiring the real Razorpay routes
 * later (F7) is a one-file swap — no caller changes.
 */
import type { CreateOrderResponse, PaymentResponse } from '../types';

const MOCK_KEY_ID = 'rzp_test_placeholder_key_id';

export async function createOrder(): Promise<CreateOrderResponse> {
  return {
    order_id: `order_mock_${Date.now()}`,
    amount: 29900, // paise — ₹299
    currency: 'INR',
    key_id: import.meta.env.VITE_RAZORPAY_KEY_ID ?? MOCK_KEY_ID,
  };
}

export async function listPayments(): Promise<PaymentResponse[]> {
  return [];
}
