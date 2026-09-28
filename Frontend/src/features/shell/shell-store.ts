/**
 * shell-store — global AppShell navigation visibility.
 *
 * Why this exists: `/app/chat` used to render TWO full sidebars side by
 * side (AppShell 248px nav + HistoryRail 280px) = 528px gone before the
 * chat even starts. Modern chat apps (ChatGPT / Claude / Perplexity) give
 * the content max space with independently collapsible rails + a
 * full-width focus mode.
 *
 * - `navOpen` persists to localStorage (`bl-nav-open`).
 * - First visit to `/app/chat` defaults to collapsed (fixes the double
 *   sidebar immediately); every other page defaults to open.
 * - An explicit stored choice always wins over the route default.
 */
import { create } from 'zustand';

const STORAGE_KEY = 'bl-nav-open';

function initialNavOpen(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === '1') return true;
    if (stored === '0') return false;
  } catch {
    /* storage unavailable — fall through to route default */
  }
  try {
    if (typeof window !== 'undefined' && window.location.pathname.startsWith('/app/chat')) {
      return false;
    }
  } catch {
    /* ignore */
  }
  return true;
}

interface ShellState {
  navOpen: boolean;
  setNavOpen: (open: boolean) => void;
  toggleNav: () => void;
}

export function persistNavOpen(open: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
  } catch {
    /* storage unavailable — visibility just won't persist */
  }
}

export const useShellStore = create<ShellState>((set) => ({
  navOpen: initialNavOpen(),
  setNavOpen: (open) => {
    persistNavOpen(open);
    set({ navOpen: open });
  },
  toggleNav: () =>
    set((state) => {
      persistNavOpen(!state.navOpen);
      return { navOpen: !state.navOpen };
    }),
}));

export const NAV_STORAGE_KEY = STORAGE_KEY;
