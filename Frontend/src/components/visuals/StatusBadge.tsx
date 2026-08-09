/**
 * StatusBadge (F4) — `status` visual (src/lib/schemas/visuals.ts). A badge
 * for the current state (on_track / at_risk / off_track) with a detail line.
 */
import { CheckCircle2, CircleX, TriangleAlert } from 'lucide-react';
import type { StatusProps } from '../../lib/schemas/visuals';

const STATUS_ICONS = {
  on_track: CheckCircle2,
  at_risk: TriangleAlert,
  off_track: CircleX,
} as const;

const STATUS_LABELS = {
  on_track: 'On track',
  at_risk: 'At risk',
  off_track: 'Off track',
} as const;

export function StatusBadge({ props }: { props: StatusProps }) {
  const { state, detail } = props;
  const StatusIcon = STATUS_ICONS[state];

  return (
    <div className="visual-status">
      <p className={`visual-status__badge visual-status__badge--${state}`}>
        <StatusIcon size={14} aria-hidden="true" />
        {STATUS_LABELS[state]}
      </p>
      <p className="visual-status__detail">{detail}</p>
    </div>
  );
}
