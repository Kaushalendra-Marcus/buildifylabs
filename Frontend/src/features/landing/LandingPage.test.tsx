import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { LandingPage } from './LandingPage'

function renderLanding() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/signup" element={<p>Signup screen</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LandingPage', () => {
  it('renders the hero, ask box, and trust band', () => {
    renderLanding()
    expect(
      screen.getByRole('heading', { name: /ask your business data anything/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Ask a business question' })).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: /not a black box/i }),
    ).toBeInTheDocument()
  })

  it('carries a typed question to signup on ask', async () => {
    sessionStorage.clear()
    const user = userEvent.setup()
    renderLanding()
    await user.type(
      screen.getByRole('textbox', { name: 'Ask a business question' }),
      'Why did revenue drop last week?',
    )
    await user.click(screen.getByRole('button', { name: /ask/i }))
    expect(sessionStorage.getItem('bl-pending-question')).toBe(
      'Why did revenue drop last week?',
    )
    expect(await screen.findByText('Signup screen')).toBeInTheDocument()
  })

  it('fills the ask box from a starter chip', async () => {
    const user = userEvent.setup()
    renderLanding()
    await user.click(screen.getByRole('button', { name: 'Forecast next quarter' }))
    expect(
      screen.getByRole('textbox', { name: 'Ask a business question' }),
    ).toHaveValue('Forecast next quarter')
  })

  it('shows an honest audience strip instead of fake customer logos', () => {
    const { container } = renderLanding()
    expect(
      screen.getByText('Built for operators who live in spreadsheets'),
    ).toBeInTheDocument()
    expect(screen.getByText('D2C founders')).toBeInTheDocument()
    for (const fake of ['Acme Corp', 'Hooli', 'Umbrella', 'Stark']) {
      expect(container.textContent).not.toContain(fake)
    }
  })

  it('reveals scroll sections and shows the fox CTA art', () => {
    const { container } = renderLanding()
    // No IntersectionObserver in jsdom — everything reveals immediately.
    for (const el of container.querySelectorAll('.bl-reveal')) {
      expect(el).toHaveClass('is-visible')
    }
    const art = container.querySelector<HTMLImageElement>('.bl-cta__art img')
    expect(art?.getAttribute('src')).toBe('/login.png')
  })
})
