import { describe, expect, it } from 'vitest';
import type { GraphProps } from '../../lib/schemas/visuals';
import type { VisualOutput } from '../../types/chat';
import { visualSubtitle, visualTypeLabel } from './visual-meta';

function graph(props: GraphProps, extra: Partial<VisualOutput> = {}): VisualOutput {
  return {
    visual_type: 'graph',
    title: 'Trend',
    props,
    ...extra,
  };
}

describe('visualSubtitle — InsightDeck chart takeaway', () => {
  it('prefers an explicit server-authored subtitle', () => {
    expect(
      visualSubtitle(
        graph(
          {
            chart_type: 'line',
            labels: ['Jan', 'Feb'],
            datasets: [{ name: 'Revenue', values: [100, 120] }],
          },
          { subtitle: 'Revenue grew 20% in February.' },
        ),
      ),
    ).toBe('Revenue grew 20% in February.');
  });

  it('derives a per-series change for a multi-series line chart', () => {
    expect(
      visualSubtitle(
        graph({
          chart_type: 'line',
          labels: ['2022', '2023', '2024'],
          datasets: [
            { name: 'Tesla', values: [100, 120, 150] },
            { name: 'BYD', values: [120, 118, 110] },
          ],
        }),
      ),
    ).toBe('Tesla +50.0% · BYD -8.3% over the period.');
  });

  it('derives the leading slice for a pie chart', () => {
    expect(
      visualSubtitle(
        graph({
          chart_type: 'pie',
          labels: ['Enterprise API', 'Other'],
          datasets: [{ name: 'Revenue', values: [60, 40] }],
        }),
      ),
    ).toBe('Enterprise API leads with 60.0% of the total.');
  });

  it('derives the high and low categories for a bar chart', () => {
    expect(
      visualSubtitle(
        graph({
          chart_type: 'bar',
          labels: ['A', 'B', 'C'],
          datasets: [{ name: 'Revenue', values: [10, 30, 20] }],
        }),
      ),
    ).toBe('B leads at 30; A is lowest at 10.');
  });

  it('falls back to provenance context when no chart takeaway can be computed', () => {
    expect(
      visualSubtitle({
        visual_type: 'metric',
        title: 'Revenue',
        props: {
          label: 'Revenue',
          value: 42,
          change_pct: null,
          direction: 'flat',
        },
        provenance: {
          metric: 'Revenue',
          timeframe: 'FY2025',
          units: 'USD',
        },
      }),
    ).toBe('Revenue · FY2025 · USD');
  });

  it('returns null rather than an empty subtitle', () => {
    expect(
      visualSubtitle({
        visual_type: 'metric',
        title: 'Revenue',
        props: {
          label: 'Revenue',
          value: 42,
          change_pct: null,
          direction: 'flat',
        },
      }),
    ).toBeNull();
  });
});

describe('visualTypeLabel', () => {
  it('labels the seven visual types', () => {
    expect(visualTypeLabel('graph')).toBe('Chart');
    expect(visualTypeLabel('metric')).toBe('Metric');
    expect(visualTypeLabel('table')).toBe('Detail');
    expect(visualTypeLabel('comparison')).toBe('Comparison');
    expect(visualTypeLabel('insight')).toBe('Insight');
    expect(visualTypeLabel('alert')).toBe('Alert');
    expect(visualTypeLabel('status')).toBe('Status');
  });
});
