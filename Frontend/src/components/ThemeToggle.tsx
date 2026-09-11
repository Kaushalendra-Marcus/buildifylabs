/**
 * ThemeToggle — the whole-app light/dark switch. Shows the icon of the mode
 * a press will move TO (moon in light, sun in dark), flips the stored
 * explicit choice (`system` resolves first), and everything re-themes via
 * the CSS tokens with no remount. Purely presentational state-wise; the
 * `theme-store` owns persistence + `<html data-theme>`.
 */
import { Moon, Sun } from 'lucide-react';
import {
  toggleThemeValue,
  useEffectiveTheme,
  useThemeStore,
} from '../lib/theme-store';
import './ThemeToggle.css';

export function ThemeToggle({ className = '' }: { className?: string }) {
  const choice = useThemeStore((state) => state.theme);
  const setTheme = useThemeStore((state) => state.setTheme);
  const effective = useEffectiveTheme();
  const next = toggleThemeValue(choice);

  return (
    <button
      type="button"
      className={`theme-toggle${className ? ` ${className}` : ''}`}
      aria-label={
        next === 'dark' ? 'Switch to dark mode' : 'Switch to light mode'
      }
      title={next === 'dark' ? 'Switch to dark mode' : 'Switch to light mode'}
      aria-pressed={effective === 'dark'}
      onClick={() => setTheme(next)}
    >
      {effective === 'dark' ? (
        <Sun size={18} aria-hidden="true" />
      ) : (
        <Moon size={18} aria-hidden="true" />
      )}
    </button>
  );
}
