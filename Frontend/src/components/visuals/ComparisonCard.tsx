/**
 * ComparisonCard (F4) — `comparison` visual (src/lib/schemas/visuals.ts).
 * `value` vs `baseline` with a computed delta, plus each `groups` member as a
 * proportional bar. The delta is computed here from the two numbers the
 * backend already returned (specs/11 §2: the LLM never does arithmetic).
 */
import { ArrowDownRight, ArrowUpRight } from 'lucide-react';
import { formatCompactNumber } from '../../lib/format';
import type { ComparisonProps } from '../../lib/schemas/visuals';

function exact(value: number): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2,
  }).format(value);
}

export function ComparisonCard({ props }: { props: ComparisonProps }) {
  const { value, baseline, groups } = props;
  const delta = value - baseline;
  const deltaPct = baseline !== 0 ? (delta / baseline) * 100 : null;
  const isUp = delta >= 0;
  const maxGroup = groups.reduce((max, group) => Math.max(max, group.value), 0);
  const DeltaIcon = isUp ? ArrowUpRight : ArrowDownRight;

  return (
    <div className="visual-comparison">
      <div className="visual-comparison__head">
        <p className="visual-comparison__value" title={exact(value)}>
          {formatCompactNumber(value)}
        </p>
        <p
          className={`visual-comparison__delta ${
            isUp
              ? 'visual-comparison__delta--up'
              : 'visual-comparison__delta--down'
          }`}
        >
          <DeltaIcon size={13} aria-hidden="true" />
          {deltaPct === null ? delta : `${deltaPct.toFixed(1)}%`}
        </p>
      </div>
      <p className="visual-comparison__baseline" title={exact(baseline)}>
        Baseline: {formatCompactNumber(baseline)}
      </p>
      <ul className="visual-comparison__groups">
        {groups.map((group) => (
          <li key={group.label} className="visual-comparison__group">
            <span className="visual-comparison__group-label">
              {group.label}
            </span>
            <span className="visual-comparison__group-track">
              <span
                className="visual-comparison__group-fill"
                style={{
                  width: `${maxGroup > 0 ? (group.value / maxGroup) * 100 : 0}%`,
                }}
              />
            </span>
            <span className="visual-comparison__group-value" title={exact(group.value)}>
              {formatCompactNumber(group.value)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
