/**
 * Source-scope store (F5, specs/14 §5.2 / 07 FR3) — the 3-way segmented
 * "Your data / Live web / Both" choice. Defaults to `own_data`, persists
 * across queries AND reloads (localStorage), and is only ever changed by the
 * user picking a segment — never silently (07 FR4).
 *
 * Gating note: `live_web` / `both` are backend-deferred (B7). The selector is
 * built now and the chosen value is sent on `/chat`; until B7 lands the
 * backend answers those scopes with its own honest fallback ("Live web ...
 * isn't available yet"). The composer shows a gating hint, never a silent
 * switch.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { SourceScope } from '../../types/chat';

interface ScopeState {
  scope: SourceScope;
  setScope(scope: SourceScope): void;
}

export const useScopeStore = create<ScopeState>()(
  persist(
    (set) => ({
      scope: 'own_data',
      setScope: (scope) => set({ scope }),
    }),
    { name: 'buildifylabs.source-scope' },
  ),
);