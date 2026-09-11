/**
 * VisualCard (F4) — specs/14 §4.2 step 2 + §8 acceptance: each of the 7
 * visual types renders its component inline via the plain type→component
 * lookup, and an unrecognized `visual_type` degrades to the fallback instead
 * of crashing the message.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VisualCard } from './VisualCard'
import type { VisualOutput } from '../../types/chat'

describe('VisualCard — type→component lookup (F4)', () => {
  it('renders a MetricCard for metric', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'metric',
          title: 'Revenue',
          props: { label: 'Revenue', value: 4.2, change_pct: 12, direction: 'up' },
        }}
      />,
    )

    expect(screen.getByText('Revenue')).toHaveClass('visual-metric__label')
    expect(screen.getByText('4.2')).toHaveClass('visual-metric__value')
    expect(screen.getByText('+12%')).toHaveClass(
      'visual-metric__change--up',
    )
  })

  it('compacts huge metric values but keeps the exact figure available', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'metric',
          title: 'Total revenue',
          props: { label: 'Total revenue', value: 47000000000, change_pct: null, direction: 'flat' },
        }}
      />,
    )

    const value = screen.getByText('47B')
    expect(value).toHaveClass('visual-metric__value')
    expect(value).toHaveAttribute('title', '47,000,000,000')
  })

  it('renders a pie graph as a donut with a total and percent legend', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'graph',
          title: 'Split',
          props: {
            chart_type: 'pie',
            labels: ['Enterprise API', 'Other'],
            datasets: [{ name: 'Revenue', values: [40, 60] }],
          },
        }}
      />,
    )

    expect(screen.getByText('100')).toHaveClass('visual-donut__total')
    expect(screen.getByText('Enterprise API')).toHaveClass('visual-donut__legend-name')
    expect(screen.getByText('40.0%')).toBeInTheDocument()
    expect(screen.getByText('60.0%')).toBeInTheDocument()
  })

  it('reads the pie ramp from the theme tokens, falling back to the dark ramp', () => {
    document.documentElement.style.setProperty('--chart-pie-1', '#123456')
    try {
      const { container, unmount } = render(
        <VisualCard
          visual={{
            visual_type: 'graph',
            title: 'Split',
            props: {
              chart_type: 'pie',
              labels: ['Enterprise API', 'Other'],
              datasets: [{ name: 'Revenue', values: [40, 60] }],
            },
          }}
        />,
      )
      const swatches = container.querySelectorAll('.visual-donut__legend li i')
      expect(swatches[0]).toHaveStyle({ background: '#123456' })
      unmount()
    } finally {
      document.documentElement.style.removeProperty('--chart-pie-1')
    }

    // No token (or jsdom without one): the dark ramp keeps old snapshots green.
    render(
      <VisualCard
        visual={{
          visual_type: 'graph',
          title: 'Split',
          props: {
            chart_type: 'pie',
            labels: ['Enterprise API', 'Other'],
            datasets: [{ name: 'Revenue', values: [40, 60] }],
          },
        }}
      />,
    )
    expect(
      document.querySelectorAll('.visual-donut__legend li i')[0],
    ).toHaveStyle({ background: '#ffbf48' })
  })

  it('renders a GraphCard for graph across line, bar, pie and area', () => {
    const chartTypes = ['line', 'bar', 'pie', 'area'] as const
    for (const chart_type of chartTypes) {
      const { unmount } = render(
        <VisualCard
          visual={{
            visual_type: 'graph',
            title: 'Trend',
            props: {
              chart_type,
              labels: ['Jan', 'Feb'],
              datasets: [{ name: 'Revenue', values: [4.2, 5.1] }],
            },
          }}
        />,
      )
      expect(
        screen.getByRole('img', { name: `${chart_type} chart` }),
      ).toBeInTheDocument()
      unmount()
    }
  })

  it('renders a BusinessSummaryTable for table', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'table',
          title: 'Rows',
          props: {
            columns: ['month', 'revenue'],
            values: [
              ['Jan', 1200],
              ['Feb', 980],
            ],
          },
        }}
      />,
    )

    expect(screen.getByRole('table')).toHaveClass('visual-table')
    expect(
      screen.getByRole('columnheader', { name: 'revenue' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Jan' })).toBeInTheDocument()
  })

  it('renders Figures cited as a real table with Figure/Context headers', () => {
    const { container } = render(
      <VisualCard
        visual={{
          visual_type: 'table',
          title: 'Figures cited',
          props: {
            columns: ['Figure', 'Context'],
            values: [
              ['18.9% [2]', 'Job outlook is exceptionally bright.'],
              ['$222,203 [3]', 'Top cities for tech jobs in 2026.'],
            ],
          },
        }}
      />,
    )

    expect(screen.getByRole('table')).toHaveClass('visual-table')
    expect(
      screen.getByRole('columnheader', { name: 'Figure' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('columnheader', { name: 'Context' }),
    ).toBeInTheDocument()
    expect(screen.getByText('18.9%')).toBeInTheDocument()
    // Rows separated by hairlines, not cards: no list markup.
    expect(container.querySelector('.visual-figures')).not.toBeInTheDocument()
  })

  it('renders Timeline as a vertical rail, not a table', () => {
    const { container } = render(
      <VisualCard
        visual={{
          visual_type: 'table',
          title: 'Timeline',
          props: {
            columns: ['Date', 'Event'],
            values: [
              ['2026-09-09', 'LEAP 2026 reflections [1]'],
              ['2026-09-08', 'Top cities for tech jobs [2]'],
            ],
          },
        }}
      />,
    )

    const rail = container.querySelector('ol.visual-timeline')
    expect(rail).toBeInTheDocument()
    expect(rail?.querySelectorAll('.visual-timeline__item')).toHaveLength(2)
    expect(rail?.querySelectorAll('.visual-timeline__dot')).toHaveLength(2)
    expect(screen.getByText('Sep 9, 2026')).toHaveClass(
      'visual-timeline__date',
    )
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders a ComparisonCard for comparison', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'comparison',
          title: 'vs baseline',
          props: {
            value: 1200,
            baseline: 1000,
            groups: [
              { label: 'North', value: 600 },
              { label: 'South', value: 400 },
            ],
          },
        }}
      />,
    )

    expect(screen.getByText('1.2K')).toHaveClass('visual-comparison__value')
    expect(screen.getByText('Baseline: 1K')).toBeInTheDocument()
    expect(screen.getByText('20.0%')).toHaveClass(
      'visual-comparison__delta--up',
    )
    expect(screen.getByText('North')).toHaveClass(
      'visual-comparison__group-label',
    )
  })

  it('renders an InsightCard for insight', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'insight',
          title: 'Why',
          props: {
            text: 'Seasonality correlates with the drop.',
            context: 'Compared to the prior six months.',
          },
        }}
      />,
    )

    expect(screen.getByText('Seasonality correlates with the drop.')).toHaveClass(
      'visual-insight__text',
    )
    expect(screen.getByText('Compared to the prior six months.')).toHaveClass(
      'visual-insight__context',
    )
  })

  it('renders an AlertList for alert', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'alert',
          title: 'Alerts',
          props: {
            level: 'critical',
            summary: 'Stock below safety level',
            reason: 'Current level is 12 vs a minimum of 40.',
          },
        }}
      />,
    )

    const summary = screen.getByText('Stock below safety level')
    expect(summary).toHaveClass('visual-alert__summary')
    expect(summary.closest('.visual-alert')).toHaveClass(
      'visual-alert--critical',
    )
  })

  it('renders a StatusBadge for status', () => {
    render(
      <VisualCard
        visual={{
          visual_type: 'status',
          title: 'Status',
          props: {
            state: 'on_track',
            detail: 'Revenue is tracking 4% ahead of plan.',
          },
        }}
      />,
    )

    expect(screen.getByText('On track')).toHaveClass('visual-status__badge')
    expect(screen.getByText('Revenue is tracking 4% ahead of plan.')).toHaveClass(
      'visual-status__detail',
    )
  })

  it('degrades gracefully to the fallback for an unknown type', () => {
    render(
      <VisualCard
        visual={
          { visual_type: 'mystery', title: 'X', props: {} } as unknown as VisualOutput
        }
      />,
    )

    expect(
      screen.getByText("This visual type isn't supported yet."),
    ).toBeInTheDocument()
    expect(screen.getByText('mystery')).toBeInTheDocument()
  })
})
