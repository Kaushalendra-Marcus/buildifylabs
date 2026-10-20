/**
 * Visual metadata helpers — the InsightDeck rule applied to real data:
 * every chart gets a short takeaway line under its title, not just an axis
 * description. The backend may supply `subtitle`; when it does not (older
 * cached answers or deterministic builders), we derive one from the chart's
 * own values. Nothing here invents a number: the fallback is a direct
 * calculation over the plotted series.
 */
import type { GraphProps } from '../../lib/schemas/visuals';
import type { VisualOutput } from '../../types/chat';
import { formatCompactNumber } from '../../lib/format';

const MAX_SUBTITLE_SERIES = 3;

function finiteValues(values: number[]): number[] {
  return values.filter((value) => Number.isFinite(value));
}

function signedPercent(value: number): string {
  const safe = Number.isFinite(value) ? value : 0;
  const sign = safe > 0 ? '+' : '';
  return `${sign}${safe.toFixed(1)}%`;
}

/** Data-backed takeaway for graph visuals (line, area, bar, pie). */
function graphTakeaway(props: GraphProps): string | null {
  const { chart_type, labels, datasets } = props;
  if (labels.length === 0 || datasets.length === 0) return null;

  if (chart_type === 'pie') {
    const values = (datasets[0]?.values ?? []).map((value) =>
      Number.isFinite(value) ? value : 0,
    );
    if (values.length < 2) return null;
    const total = values.reduce((sum, value) => sum + Math.max(0, value), 0);
    if (total <= 0) return null;
    const topIndex = values.reduce(
      (best, value, index) => (value > values[best] ? index : best),
      0,
    );
    const share = (Math.max(0, values[topIndex] ?? 0) / total) * 100;
    return `${labels[topIndex] ?? 'The top category'} leads with ${share.toFixed(1)}% of the total.`;
  }

  if (chart_type === 'bar') {
    const series = datasets[0];
    const values = (series?.values ?? []).map((value) =>
      Number.isFinite(value) ? value : 0,
    );
    if (values.length < 2) return null;
    const maxIndex = values.reduce(
      (best, value, index) => (value > values[best] ? index : best),
      0,
    );
    const minIndex = values.reduce(
      (best, value, index) => (value < values[best] ? index : best),
      0,
    );
    if (maxIndex === minIndex) return null;
    const prefix = datasets.length > 1 ? `${series?.name ?? 'Series'}: ` : '';
    return `${prefix}${labels[maxIndex] ?? 'Top'} leads at ${formatCompactNumber(values[maxIndex])}; ${labels[minIndex] ?? 'lowest'} is lowest at ${formatCompactNumber(values[minIndex])}.`;
  }

  // line / area — compare first and last plotted points per series.
  const moves = datasets
    .slice(0, MAX_SUBTITLE_SERIES)
    .map((dataset) => {
      const values = finiteValues(dataset.values);
      if (values.length < 2) return null;
      const first = values[0];
      const last = values[values.length - 1];
      if (first === 0) return null;
      const change = ((last - first) / Math.abs(first)) * 100;
      return {
        label: dataset.name,
        change: signedPercent(change),
      };
    })
    .filter(
      (move): move is { label: string; change: string } => Boolean(move),
    );

  if (moves.length === 0) return null;
  if (datasets.length === 1) {
    return `${moves[0].change} over ${labels.length} periods.`;
  }
  return `${moves.map((move) => `${move.label} ${move.change}`).join(' · ')} over the period.`;
}

/** Context fallback from provenance: "Revenue · FY2025 · USD". */
function provenanceContext(visual: VisualOutput): string | null {
  const provenance = visual.provenance;
  if (!provenance || typeof provenance !== 'object') return null;
  const parts = [provenance.metric, provenance.timeframe, provenance.units]
    .filter((part): part is string => typeof part === 'string' && part.trim().length > 0)
    .map((part) => part.trim())
    .slice(0, 3);
  return parts.length > 0 ? parts.join(' · ') : null;
}

/**
 * The line rendered under a visual title. Order: server-authored insight →
 * calculated takeaway → provenance context. Returns null rather than an
 * empty shell when no honest subtitle exists.
 */
export function visualSubtitle(visual: VisualOutput): string | null {
  const explicit = visual.subtitle?.trim();
  if (explicit) return explicit;
  if (visual.visual_type === 'graph') {
    const takeaway = graphTakeaway(visual.props as GraphProps);
    if (takeaway) return takeaway;
  }
  return provenanceContext(visual);
}

/** Short label for the presentation slide kicker. */
export function visualTypeLabel(type: VisualOutput['visual_type']): string {
  switch (type) {
    case 'metric':
      return 'Metric';
    case 'graph':
      return 'Chart';
    case 'table':
      return 'Detail';
    case 'comparison':
      return 'Comparison';
    case 'insight':
      return 'Insight';
    case 'alert':
      return 'Alert';
    case 'status':
      return 'Status';
    default:
      return 'Visual';
  }
}
