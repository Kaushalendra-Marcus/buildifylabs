/**
 * AlertList (F4) — `alert` visual (src/lib/schemas/visuals.ts). An alert row
 * with an icon + colour that match its level (info / warning / critical).
 */
import { AlertTriangle, CircleAlert, Info } from 'lucide-react';
import type { AlertProps } from '../../lib/schemas/visuals';

const ALERT_ICONS = {
  info: Info,
  warning: AlertTriangle,
  critical: CircleAlert,
} as const;

export function AlertList({ props }: { props: AlertProps }) {
  const { level, summary, reason } = props;
  const AlertIcon = ALERT_ICONS[level];

  return (
    <ul className="visual-alert-list">
      <li className={`visual-alert visual-alert--${level}`}>
        <AlertIcon size={15} aria-hidden="true" />
        <div className="visual-alert__body">
          <p className="visual-alert__summary">{summary}</p>
          <p className="visual-alert__reason">{reason}</p>
        </div>
      </li>
    </ul>
  );
}
