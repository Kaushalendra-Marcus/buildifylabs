/**
 * formatRemaining — turn ms-until-something into the chip/notice label
 * (specs/14 §5.5 "· resets in 4h"). Compact: `4h`, `45m`, `3h 20m`.
 */

export function formatRemaining(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return 'shortly';
  const totalMinutes = Math.max(1, Math.floor(ms / 60_000));
  if (totalMinutes < 60) {
    return `${totalMinutes}m`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`;
}

/**
 * formatTime — epoch ms to the short "12:28 AM" stamp shown under user
 * bubbles and at the end of the trust-footer row. Pure (no `Date.now()`),
 * so rendering a stored timestamp stays deterministic.
 */
export function formatTime(epochMs: number): string {
  return new Date(epochMs).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * formatCompactNumber — headline-size numbers for metric values and donut
 * totals: 47,000,000,000 → "47B", 1,200 → "1.2K", 4.2 → "4.2". Pair with the
 * exact figure in a `title` tooltip so nothing is lost.
 */
export function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value);
}