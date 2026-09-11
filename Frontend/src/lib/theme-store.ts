/**
 * Theme store — whole-app light / dark mode (specs/14 cross-cutting).
 *
 * `theme` is the user's explicit choice, persisted across reloads
 * (localStorage, same pattern as `scope-store`): `'light'` / `'dark'` set
 * `<html data-theme="…">` (see `src/index.css`), while `'system'` (default)
 * stores no attribute and follows the OS `prefers-color-scheme` query.
 * Components never read this to branch rendering — everything keys off the
 * CSS custom-property tokens, so a theme switch is a single attribute flip
 * with no remount. The one exception is `GraphCard`'s pie ramp, which reads
 * the `--chart-pie-*` tokens via `getComputedStyle` at render.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { useMediaQuery } from '../hooks/useMediaQuery';

export type ThemeChoice = 'light' | 'dark' | 'system';
export type EffectiveTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'buildifylabs.theme';
const THEME_ATTRIBUTE = 'data-theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';

function systemIsDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia(DARK_QUERY).matches;
}

export function resolveTheme(choice: ThemeChoice): EffectiveTheme {
  if (choice === 'light') return 'light';
  if (choice === 'dark') return 'dark';
  return systemIsDark() ? 'dark' : 'light';
}

/** Apply a choice to `<html>` — idempotent, safe to call on every render. */
export function applyTheme(choice: ThemeChoice): void {
  if (typeof document === 'undefined') return;
  if (choice === 'system') {
    document.documentElement.removeAttribute(THEME_ATTRIBUTE);
  } else {
    document.documentElement.setAttribute(THEME_ATTRIBUTE, choice);
  }
}

interface ThemeState {
  theme: ThemeChoice;
  setTheme(theme: ThemeChoice): void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: 'system',
      setTheme: (theme) => set({ theme }),
    }),
    { name: THEME_STORAGE_KEY },
  ),
);

/** Live effective theme (resolves `system` via the OS query, reactive). */
export function useEffectiveTheme(): EffectiveTheme {
  const choice = useThemeStore((state) => state.theme);
  const osDark = useMediaQuery(DARK_QUERY);
  if (choice === 'light') return 'light';
  if (choice === 'dark') return 'dark';
  return osDark ? 'dark' : 'light';
}

/** Flip to the opposite of the current effective theme (toggle buttons). */
export function toggleThemeValue(choice: ThemeChoice): ThemeChoice {
  return resolveTheme(choice) === 'dark' ? 'light' : 'dark';
}
