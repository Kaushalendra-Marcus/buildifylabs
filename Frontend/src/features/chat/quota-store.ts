/**
 * Quota tracker store — client-side mirror of the backend rule (specs/02
 * FR1/FR2: 4 questions / rolling 6h, 100 lifetime, everyone equal). Persisted
 * to localStorage so the ambient "3 of 4 left · resets in 4h" chip (specs/14
 * §5.5) survives reloads.
 *
 * IMPORTANT: the backend is the authority — its atomic quota UPDATE and 429
 * cannot be fooled by this store. This exists only to *display* the rolling
 * window before it's exhausted; API outcomes keep it honest:
 *   - a successful `/chat` → `recordQuestion()`
 *   - a 429 window response  → `applyWindowExhausted()`
 *   - a 429 lifetime response → `applyLifetimeExhausted()`
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const WINDOW_QUESTIONS_LIMIT = 4;
export const WINDOW_HOURS = 6;
export const LIFETIME_QUESTIONS_LIMIT = 100;
export const WINDOW_MS = WINDOW_HOURS * 60 * 60 * 1000;

interface QuotaState {
  questionsInWindow: number;
  windowStartedAt: number | null; // epoch ms
  questionsLifetime: number;
  recordQuestion(): void;
  applyWindowExhausted(): void;
  applyLifetimeExhausted(): void;
  reset(): void;
}

function rollIfElapsed(state: QuotaState): Pick<QuotaState, 'questionsInWindow' | 'windowStartedAt'> {
  if (state.windowStartedAt === null) {
    return { questionsInWindow: 1, windowStartedAt: Date.now() };
  }
  if (Date.now() - state.windowStartedAt >= WINDOW_MS) {
    return { questionsInWindow: 1, windowStartedAt: Date.now() };
  }
  return { questionsInWindow: state.questionsInWindow + 1, windowStartedAt: state.windowStartedAt };
}

export const useQuotaStore = create<QuotaState>()(
  persist(
    (set, get) => ({
      questionsInWindow: 0,
      windowStartedAt: null,
      questionsLifetime: 0,

      recordQuestion: () => {
        const rolled = rollIfElapsed(get());
        set({
          ...rolled,
          questionsLifetime: get().questionsLifetime + 1,
        });
      },

      applyWindowExhausted: () => {
        set({ questionsInWindow: WINDOW_QUESTIONS_LIMIT });
      },

      applyLifetimeExhausted: () => {
        set({ questionsLifetime: LIFETIME_QUESTIONS_LIMIT });
      },

      reset: () => set({ questionsInWindow: 0, windowStartedAt: null, questionsLifetime: 0 }),
    }),
    { name: 'buildifylabs.quota' },
  ),
);
