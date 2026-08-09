/**
 * useNow — a ticking "current time" for live countdown labels (quota chip
 * "· resets in 4h", specs/14 §5.5, and the window-exhausted notice).
 *
 * Purity rule (react-hooks/purity, F0 decision): `Date.now()` is never read
 * during render. The value is `null` on first render and set inside a
 * `setInterval` effect (an event, allowed) on mount, then kept fresh.
 */
import { useEffect, useState } from 'react';

export function useNow(intervalMs = 30_000): number | null {
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    const update = () => setNow(Date.now());
    update();
    const id = window.setInterval(update, intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);

  return now;
}