/**
 * QuotaChip (F2 shell + F5 label) — ambient rolling-window quota (specs/14 §3
 * header, §5.5): "3 of 4 left · resets in 4h". Reads the client-side tracker
 * (useQuota) — the backend 429 stays authoritative for enforcement. The live
 * "· resets in Xh/Ym" countdown is computed here from the exposed `resetsAt`
 * timestamp via useNow (never `Date.now()` during render).
 */
import { useNow } from '../../hooks/useNow';
import { formatRemaining } from '../../lib/format';
import { useQuota } from '../../hooks/useQuota';
import { WINDOW_QUESTIONS_LIMIT } from './quota-store';

export function QuotaChip() {
  const { leftInWindow, resetsAt } = useQuota();
  const now = useNow(30_000);

  return (
    <span className="quota-chip" data-low={leftInWindow <= 1 ? 'true' : 'false'}>
      <span className="quota-chip__count">
        {leftInWindow} of {WINDOW_QUESTIONS_LIMIT} left
      </span>
      {resetsAt !== null && now !== null && (
        <>
          {' '}
          · resets in <span className="quota-chip__resets">{formatRemaining(resetsAt - now)}</span>
        </>
      )}
    </span>
  );
}