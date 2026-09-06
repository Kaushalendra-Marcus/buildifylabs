/**
 * QuotaChip (F2 shell + F5 label) tests — the ambient rolling-window chip
 * (specs/14 §5.5): "N of 100 left · resets in 6h" (100 for testing), with the live resets-in
 * countdown derived from the client mirror's `resetsAt` timestamp.
 */
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { QuotaChip } from './QuotaChip'
import { useQuotaStore } from './quota-store'

beforeEach(() => {
  localStorage.clear()
  useQuotaStore.setState({
    questionsInWindow: 0,
    windowStartedAt: null,
    questionsLifetime: 0,
  })
})

describe('QuotaChip (F5 §5.5)', () => {
  it('shows "N of 100 left" with a live resets-in countdown once a window started', async () => {
    // Window started 2h ago → exactly ~4h remain in the rolling 6h window.
    useQuotaStore.setState({
      questionsInWindow: 1,
      windowStartedAt: Date.now() - 2 * 60 * 60 * 1000,
    })

    render(<QuotaChip />)

    expect(screen.getByText('99 of 100 left')).toBeInTheDocument()
    expect(await screen.findByText(/resets in/)).toBeInTheDocument()
  })

  it('marks the chip as low-warning at 1 question left', () => {
    useQuotaStore.setState({ questionsInWindow: 99, windowStartedAt: Date.now() })
    render(<QuotaChip />)
    expect(screen.getByText('1 of 100 left').closest('.quota-chip')).toHaveAttribute(
      'data-low',
      'true',
    )
  })

  it('shows no countdown before the first question starts the window', () => {
    render(<QuotaChip />)
    expect(screen.getByText('100 of 100 left')).toBeInTheDocument()
    expect(screen.queryByText(/resets in/)).not.toBeInTheDocument()
  })
})