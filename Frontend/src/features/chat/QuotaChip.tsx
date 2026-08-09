/**
 * QuotaChip (F2 shell) — ambient rolling-window quota in the header
 * (specs/14 §3 header, §5.5): "3 of 4 left". Reads the client-side tracker
 * (useQuota) — the backend 429 stays authoritative for enforcement. The live
 * "· resets in 4h" countdown label is the F5 chip's job from `resetsAt`.
 */
import { useQuota } from '../../hooks/useQuota';

export function QuotaChip() {
  const { leftInWindow } = useQuota();

  return (
    <span className="quota-chip" data-low={leftInWindow <= 1 ? 'true' : 'false'}>
      {leftInWindow} of 4 left
    </span>
  );
}