import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { useThemeStore } from '../../lib/theme-store'
import { LandingPage } from './LandingPage'

beforeEach(() => {
  localStorage.clear()
  useThemeStore.setState({ theme: 'system' })
  document.documentElement.removeAttribute('data-theme')
})

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

  it('offers a dark/light switch in the nav that flips the stored theme', async () => {
    const user = userEvent.setup()
    const { container } = renderLanding()
    const toggle = screen.getByRole('button', { name: /switch to (dark|light) mode/i })
    expect(container.querySelector('.bl-nav__actions')).toContainElement(toggle)

    await user.click(toggle)
    expect(['light', 'dark']).toContain(useThemeStore.getState().theme)
  })

  it('tabs the hero demo between three different example layouts', async () => {
    const user = userEvent.setup()
    renderLanding()
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(3)
    const panel = screen.getByRole('tabpanel')

    // Diagnose-a-drop: metric + bars + data table.
    expect(within(panel).getByText('Why did revenue drop last week?')).toBeInTheDocument()
    expect(within(panel).getByText('WoW')).toBeInTheDocument()
    expect(panel.querySelector('.bl-mock__table')).not.toBeNull()

    // Head-to-head comparison: versus cards + share bars, no table.
    await user.click(screen.getByRole('tab', { name: 'Region compare' }))
    expect(within(panel).getByText('Compare sales by region')).toBeInTheDocument()
    expect(within(panel).getByText(/east leads the quarter/i)).toBeInTheDocument()
    expect(within(panel).getByLabelText('Revenue share by region')).toBeInTheDocument()
    expect(panel.querySelector('.bl-mock__table')).toBeNull()

    // Forecast: full chart with axes, values, and months — no table.
    await user.click(screen.getByRole('tab', { name: 'Forecast' }))
    expect(within(panel).getByText('Forecast next quarter')).toBeInTheDocument()
    expect(within(panel).getByText(/pace suggests/i)).toBeInTheDocument()
    expect(within(panel).getByText('Possible factors')).toBeInTheDocument()
    const svg = panel.querySelector('.bl-mock__spark-svg')
    expect(svg).not.toBeNull()
    const svgText = svg?.textContent ?? ''
    for (const label of ['$50k', '$70k', '$58.2k', '$66.0k', 'Aug', 'Dec']) {
      expect(svgText).toContain(label)
    }
    expect(svg?.querySelectorAll('circle').length).toBe(5)
    expect(panel.querySelector('.bl-mock__table')).toBeNull()
  })
})
