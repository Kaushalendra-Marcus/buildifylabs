/**
 * ScopePopover tests — the mobile-compact source + quota control. Picking
 * an option applies the scope and closes; the quota line mirrors the chip.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useQuotaStore } from './quota-store'
import { ScopePopover } from './ScopePopover'
import { useScopeStore } from './scope-store'

beforeEach(() => {
  localStorage.clear()
  useScopeStore.setState({ scope: 'own_data' })
  useQuotaStore.setState({
    questionsInWindow: 0,
    windowStartedAt: null,
    questionsLifetime: 0,
  })
  vi.clearAllMocks()
})

describe('ScopePopover', () => {
  it('applies the picked scope and closes', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<ScopePopover onClose={onClose} />)

    expect(screen.getByText(/100 of 100 left/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Live web/ }))
    expect(useScopeStore.getState().scope).toBe('live_web')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('marks the active scope pressed', () => {
    useScopeStore.setState({ scope: 'both' })
    render(<ScopePopover onClose={() => undefined} />)
    expect(screen.getByRole('button', { name: /^Both/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })
})
