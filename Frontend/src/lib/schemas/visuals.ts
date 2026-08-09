/**
 * Per-`visual_type` props — SINGLE SOURCE OF TRUTH (F0 contract freeze).
 *
 * The backend's `PipelineOutput.visuals[].props` (specs/06 §3) must match these
 * shapes; the 7 visual components (F4) render from them. `specs/06` §3 defers to
 * this file explicitly, so if a props shape needs to change, change it HERE
 * first, then update the backend prompt/spec references.
 *
 * The backend only constrains the type *values* (`Literal[7]`); the shapes below
 * are what each component actually reads. `VisualProps` is the discriminated
 * union a renderer narrows on `visual_type` to get typed props.
 */
export const VISUAL_TYPES = [
  'metric',
  'graph',
  'table',
  'comparison',
  'insight',
  'alert',
  'status',
] as const;

export type VisualType = (typeof VISUAL_TYPES)[number];

export interface MetricProps {
  label: string;
  value: number;
  change_pct: number | null;
  direction: 'up' | 'down' | 'flat';
}

export interface GraphProps {
  chart_type: 'line' | 'bar' | 'pie' | 'area';
  labels: string[];
  datasets: { name: string; values: number[] }[];
}

export interface TableProps {
  columns: string[];
  values: (string | number)[][];
}

export interface ComparisonProps {
  value: number;
  baseline: number;
  groups: { label: string; value: number }[];
}

export interface InsightProps {
  text: string;
  context: string;
}

export interface AlertProps {
  level: 'info' | 'warning' | 'critical';
  summary: string;
  reason: string;
}

export interface StatusProps {
  state: 'on_track' | 'at_risk' | 'off_track';
  detail: string;
}

/** Map each visual_type to its props shape. `VisualProps` is the union of all
 *  seven; renderers switch on `visual_type` and get the matching type below. */
export interface VisualPropsByType {
  metric: MetricProps;
  graph: GraphProps;
  table: TableProps;
  comparison: ComparisonProps;
  insight: InsightProps;
  alert: AlertProps;
  status: StatusProps;
}

export type VisualProps = VisualPropsByType[VisualType];

/** Runtime guard for the 7 allowed values (specs/14 §8: the backend's Literal
 *  enum isn't guaranteed at runtime — degrade, don't crash, on unknowns). */
export function isVisualType(value: unknown): value is VisualType {
  return (
    typeof value === 'string' &&
    (VISUAL_TYPES as readonly string[]).includes(value)
  );
}
