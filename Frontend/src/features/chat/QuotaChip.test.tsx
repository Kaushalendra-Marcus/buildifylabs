/**
 * QuotaChip (F2 shell + F5 label) tests — the ambient rolling-window chip
 * (specs/14 §5.5): "3 of 4 left · resets in 4h", with the live resets-in
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
  it('shows "N of 4 left" with a live resets-in countdown once a window started', async () => {
    // Window started 2h ago → exactly ~4h remain in the rolling 6h window.
    useQuotaStore.setState({
      questionsInWindow: 1,
      windowStartedAt: Date.now() - 2 * 60 * 60 * 1000,
    })

    render(<QuotaChip />)

    expect(screen.getByText('3 of 4 left')).toBeInTheDocument()
    expect(await screen.findByText(/resets in/)).toBeInTheDocument()
  })

  it('marks the chip as low-warning at 1 question left', () => {
    useQuotaStore.setState({ questionsInWindow: 3, windowStartedAt: Date.now() })
    render(<QuotaChip />)
    expect(screen.getByText('1 of 4 left').closest('.quota-chip')).toHaveAttribute(
      'data-low',
      'true',
    )
  })

  it('shows no countdown before the first question starts the window', () => {
    render(<QuotaChip />)
    expect(screen.getByText('4 of 4 left')).toBeInTheDocument()
    expect(screen.queryByText(/resets in/)).not.toBeInTheDocument()
  })
})