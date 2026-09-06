import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { GooLoader } from './GooLoader'

describe('GooLoader', () => {
  it('renders the author verbatim structure with an accessible label', () => {
    const { container } = render(<GooLoader label="Loading answers" />)

    expect(screen.getByRole('img', { name: 'Loading answers' })).toBeInTheDocument()
    // Verbatim snippet shape: ring (::before) + gradient box + 100x100 svg
    // with a 7-polygon mask (1 black backdrop + 6 white triangles).
    const mask = container.querySelector('svg mask')
    expect(mask).toBeInTheDocument()
    expect(mask?.querySelectorAll('polygon')).toHaveLength(7)
    expect(container.querySelector('.goo-loader__box')).toBeInTheDocument()
  })

  it('is decorative (aria-hidden, no image role) without a label', () => {
    const { container } = render(<GooLoader />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(
      container.querySelector('.goo-loader')?.getAttribute('aria-hidden'),
    ).toBe('true')
  })

  it('gives every instance a unique mask id wired to its own box', () => {
    const { container } = render(
      <>
        <GooLoader label="First" />
        <GooLoader label="Second" />
      </>,
    )

    const masks = [...container.querySelectorAll('svg mask')]
    expect(masks).toHaveLength(2)
    const [first, second] = masks.map((mask) => mask.getAttribute('id'))
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(first).not.toBe(second)

    const boxes = [...container.querySelectorAll('.goo-loader__box')]
    // (jsdom serializes url(#id) with quotes — match the id itself.)
    expect(boxes[0].getAttribute('style')).toContain(`#${first}`)
    expect(boxes[1].getAttribute('style')).toContain(`#${second}`)
  })

  it('applies the size prop as the snippet scale variable', () => {
    const { container } = render(<GooLoader label="Small" size={0.5} />)

    expect(
      container
        .querySelector('.goo-loader')
        ?.getAttribute('style'),
    ).toContain('--goo-size')
  })
})
