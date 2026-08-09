/**
 * useMediaQuery — subscribes a component to a CSS media query (used by the
 * chat workspaces's rail breakpoint, specs/14 §3, "collapsed by default
 * <768px"). Fallbacks to `false` when `matchMedia` is unavailable (e.g. jsdom
 * tests) so the shell never crashes off-the-browser.
 */
import { useEffect, useState } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const media = window.matchMedia(query);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}