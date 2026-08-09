/**
 * VisualCard (F4) — the plain type→component lookup for the 7 visual types
 * (specs/14 §4.2 step 2 + §8: "no interception layer"). Each visual renders
 * inline via its matching component; an unrecognized `visual_type` degrades
 * to UnknownVisualCard rather than crashing the message.
 *
 * The `props` casts below are the single boundary between the frozen
 * contract's union type (`VisualProps`) and each component's narrowed props —
 * the backend contract (visuals.ts) guarantees the shape per type.
 */
import type {
  AlertProps,
  ComparisonProps,
  GraphProps,
  InsightProps,
  MetricProps,
  StatusProps,
  TableProps,
} from '../../lib/schemas/visuals';
import type { VisualOutput } from '../../types/chat';
import { AlertList } from './AlertList';
import { BusinessSummaryTable } from './BusinessSummaryTable';
import { ComparisonCard } from './ComparisonCard';
import { GraphCard } from './GraphCard';
import { InsightCard } from './InsightCard';
import { MetricCard } from './MetricCard';
import { StatusBadge } from './StatusBadge';
import { UnknownVisualCard } from './UnknownVisualCard';
import './visuals.css';

interface VisualCardProps {
  visual: VisualOutput;
}

export function VisualCard({ visual }: VisualCardProps) {
  switch (visual.visual_type) {
    case 'metric':
      return <MetricCard props={visual.props as MetricProps} />;
    case 'graph':
      return <GraphCard props={visual.props as GraphProps} />;
    case 'table':
      return <BusinessSummaryTable props={visual.props as TableProps} />;
    case 'comparison':
      return <ComparisonCard props={visual.props as ComparisonProps} />;
    case 'insight':
      return <InsightCard props={visual.props as InsightProps} />;
    case 'alert':
      return <AlertList props={visual.props as AlertProps} />;
    case 'status':
      return <StatusBadge props={visual.props as StatusProps} />;
    default:
      return <UnknownVisualCard visual={visual} />;
  }
}
