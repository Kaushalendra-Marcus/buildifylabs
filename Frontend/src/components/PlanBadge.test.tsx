import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PlanBadge } from './PlanBadge'

describe('PlanBadge (F1)', () => {
  it('renders a label for each plan', () => {
    const { rerender } = render(<PlanBadge plan="guest" />)
    expect(screen.getByText('guest')).toBeInTheDocument()

    rerender(<PlanBadge plan="free" />)
    expect(screen.getByText('free')).toBeInTheDocument()

    rerender(<PlanBadge plan="pro" />)
    expect(screen.getByText('pro')).toBeInTheDocument()
  })

  it('degrades gracefully for an unknown plan value', () => {
    // @ts-expect-error — defensive: backend enum not guaranteed at runtime
    render(<PlanBadge plan="enterprise" />)
    expect(screen.getByText('free')).toBeInTheDocument()
  })
})
