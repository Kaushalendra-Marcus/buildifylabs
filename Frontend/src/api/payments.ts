/**
 * Payments are intentionally unavailable until the server-side Razorpay
 * order/webhook flow from specs/03 is implemented. Never return a fabricated
 * order: callers must show an unavailable state instead of accepting a fake
 * payment.
 */
export async function createOrder(): Promise<never> {
  throw new Error(
    'Payments are not available yet. No order was created and no charge was made.',
  );
}

export async function listPayments(): Promise<never> {
  throw new Error('Payment history is not available yet.');
}
