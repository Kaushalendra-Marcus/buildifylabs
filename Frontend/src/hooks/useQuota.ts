/**
 * useQuota — exposes the client-side rolling-window tracker
 * (`src/features/chat/quota-store`) to the ambient quota chip (specs/14 §5.5).
 * The backend 429 remains authoritative for enforcement; this only renders the
 * pre-exhaustion countdown and the post-429 states (F5 wires those UI states to
 * `applyWindowExhausted` / `applyLifetimeExhausted`).
 *
 * Purity note (react-hooks/purity): no `Date.now()` is read during render — the
 * store rolls the window at `recordQuestion` time (an event, allowed), and the
 * hook exposes the fixed `resetsAt` timestamp. The "resets in 4h" label is a
 * live countdown and is rendered by the chip component (F5) from its own timer.
 */
import {
  LIFETIME_QUESTIONS_LIMIT,
  WINDOW_MS,
  WINDOW_QUESTIONS_LIMIT,
  useQuotaStore,
} from '../features/chat/quota-store';

export function useQuota() {
  const questionsInWindow = useQuotaStore((s) => s.questionsInWindow);
  const windowStartedAt = useQuotaStore((s) => s.windowStartedAt);
  const questionsLifetime = useQuotaStore((s) => s.questionsLifetime);
  const recordQuestion = useQuotaStore((s) => s.recordQuestion);
  const applyWindowExhausted = useQuotaStore((s) => s.applyWindowExhausted);
  const applyLifetimeExhausted = useQuotaStore((s) => s.applyLifetimeExhausted);

  const lifetimeLeft = Math.max(
    0,
    LIFETIME_QUESTIONS_LIMIT - questionsLifetime,
  );

  return {
    leftInWindow: Math.max(0, WINDOW_QUESTIONS_LIMIT - questionsInWindow),
    windowStartedAt,
    /** Epoch ms when the current window ends — the chip renders the countdown
     *  from this fixed timestamp. Null ⇒ no window started yet. */
    resetsAt: windowStartedAt === null ? null : windowStartedAt + WINDOW_MS,
    lifetimeLeft,
    lifetimeReached: lifetimeLeft <= 0,
    recordQuestion,
    applyWindowExhausted,
    applyLifetimeExhausted,
  };
}
