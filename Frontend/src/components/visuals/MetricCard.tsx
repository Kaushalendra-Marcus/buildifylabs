/**
 * MetricCard (F4) — `metric` visual (src/lib/schemas/visuals.ts). A headline
 * number with its label and a directional change badge. The value uses the
 * metric type scale (specs/14 §7); change direction carries meaning (up →
 * success, down → danger, flat → muted), never plain accent.
 */
import { Minus, TrendingDown, TrendingUp } from 'lucide-react';
import type { MetricProps } from '../../lib/schemas/visuals';

const CHANGE_ICONS = {
  up: TrendingUp,
  down: TrendingDown,
  flat: Minus,
} as const;

export function MetricCard({ props }: { props: MetricProps }) {
  const { label, value, change_pct, direction } = props;
  const ChangeIcon = CHANGE_ICONS[direction];
  const formatted = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2,
  }).format(value);

  return (
    <div className="visual-metric">
      <p className="visual-metric__label">{label}</p>
      <p className="visual-metric__value">{formatted}</p>
      {change_pct !== null && (
        <p
          className={`visual-metric__change visual-metric__change--${direction}`}
        >
          <ChangeIcon size={13} aria-hidden="true" />
          {change_pct > 0 ? '+' : ''}
          {change_pct}%
        </p>
      )}
    </div>
  );
}
