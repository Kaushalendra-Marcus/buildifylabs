/**
 * WindowExhaustedNotice (F5, specs/14 §5.6 / 02 FR5) — the transient inline
 * quota 429 state. An inline notice in the message stream with the reset time;
 * the input stays enabled for the next window (it is NEVER blocked by quota).
 */
import { Clock } from 'lucide-react';
import { useNow } from '../../../hooks/useNow';
import { formatRemaining } from '../../../lib/format';
import { WINDOW_QUESTIONS_LIMIT } from '../quota-store';

export function WindowExhaustedNotice({ resetAt }: { resetAt: number | null }) {
  const now = useNow(30_000);
  const remaining = now !== null && resetAt !== null ? formatRemaining(resetAt - now) : null;

  return (
    <div className="quota-notice quota-notice--window" role="status">
      <Clock size={15} aria-hidden="true" />
      <span>
        You've used your {WINDOW_QUESTIONS_LIMIT} questions for this 6-hour window.
        More unlock{' '}
        {remaining ? (
          <strong>in {remaining}</strong>
        ) : (
          'in a bit'
        )}
      </span>
    </div>
  );
}