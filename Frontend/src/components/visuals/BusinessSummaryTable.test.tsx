/**
 * Figures-cited prose cleanup: search providers join excerpt fragments with
 * "..." — that must never show up as dots in the middle of a sentence.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VisualCard } from './VisualCard'

function figuresTable(rows: [string, string][]) {
  return (
    <VisualCard
      visual={{
        visual_type: 'table',
        title: 'Figures cited',
        props: { columns: ['Figure', 'Context'], values: rows },
      }}
    />
  )
}

describe('BusinessSummaryTable — Figures cited prose', () => {
  it('turns "...:" joins and inner "..." into clean text, and marks the cut once', () => {
    render(
      figuresTable([
        [
          '₹100 crore [2]',
          "Media Mogul's ...: it became a revenue generator ... will cross",
        ],
      ]),
    )

    expect(
      screen.getByText(
        "Media Mogul's: it became a revenue generator – will cross…",
      ),
    ).toBeInTheDocument()
  })

  it('does not delete real short words at the start of an excerpt', () => {
    render(figuresTable([['$5M [1]', 'it became a revenue generator.']]))

    expect(
      screen.getByText('…it became a revenue generator.'),
    ).toBeInTheDocument()
  })

  it('shows the unit in the figure cell', () => {
    render(figuresTable([['₹100 crore [2]', 'Revenue reached the mark.']]))

    expect(screen.getByText('₹100 crore')).toBeInTheDocument()
  })
})
