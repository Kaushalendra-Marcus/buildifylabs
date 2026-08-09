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