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

    expect(screen.getByText('1200')).toHaveClass('visual-comparison__value')
    expect(screen.getByText('Baseline: 1000')).toBeInTheDocument()
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
