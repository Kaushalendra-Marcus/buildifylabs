/**
 * BoxLoader tests — the namespaced Uiverse blocks figure: all eight boxes
 * plus the ground, skinned amber, with md/sm sizing.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BoxLoader } from './BoxLoader'

describe('BoxLoader', () => {
  it('renders eight assembling boxes and the ground', () => {
    const { container } = render(<BoxLoader label="Assembling answer blocks" />)

    expect(
      screen.getByRole('img', { name: 'Assembling answer blocks' }),
    ).toBeInTheDocument()
    expect(container.querySelectorAll('.bl-boxloader__box')).toHaveLength(8)
    expect(container.querySelector('.bl-boxloader__ground')).toBeInTheDocument()
  })

  it('hides from assistive tech when purely decorative', () => {
    const { container } = render(<BoxLoader />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(
      container.querySelector('.bl-boxloader[aria-hidden="true"]'),
    ).toBeInTheDocument()
  })

  it('scales down via the sm variant without generic colliding classes', () => {
    const { container } = render(<BoxLoader size="sm" />)

    expect(container.querySelector('.bl-boxloader--sm')).toBeInTheDocument()
    expect(container.querySelector('.loader')).toBeNull()
    expect(container.querySelector('.box')).toBeNull()
  })
})
