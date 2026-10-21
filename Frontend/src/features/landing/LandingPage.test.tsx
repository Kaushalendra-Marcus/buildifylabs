import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { useThemeStore } from '../../lib/theme-store'
import { LandingPage } from './LandingPage'

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  useThemeStore.setState({ theme: 'system' })
  document.documentElement.removeAttribute('data-theme')
})

function renderLanding() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/signup" element={<p>Signup screen</p>} />
        <Route path="/signin" element={<p>Signin screen</p>} />
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
    const nav = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(nav).getByRole('link', { name: /start free/i })).toHaveAttribute(
      'href',
      '/signup',
    )
  })

  it('carries a typed question to signup on ask', async () => {
    const user = userEvent.setup()
    renderLanding()
    await user.type(
      screen.getByRole('textbox', { name: 'Ask a business question' }),
      'Why did revenue drop last week?',
    )
    await user.click(screen.getByRole('button', { name: /^ask$/i }))
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

  it('reveals scroll sections and shows the brand art in the CTA', () => {
    const { container } = renderLanding()
    // No IntersectionObserver in jsdom — everything reveals immediately.
    for (const el of container.querySelectorAll('.bl-reveal')) {
      expect(el).toHaveClass('is-visible')
    }
    const art = container.querySelector<HTMLImageElement>('.bl-cta__mascot img')
    expect(art?.getAttribute('src')).toBe('/logo.png')
  })

  it('closes the page with the mascot scene above the footer links', () => {
    const { container } = renderLanding()
    const scene = container.querySelector<HTMLImageElement>('.bl-footer__scene img')
    expect(scene?.getAttribute('src')).toBe('/login.png')
    expect(scene?.getAttribute('alt')).toMatch(/fox/i)
    // The illustration sits before the footer link columns, inside the footer.
    const footer = container.querySelector('.bl-footer')
    expect(footer?.firstElementChild?.classList.contains('bl-footer__scene')).toBe(
      true,
    )
  })

  it('offers a dark/light switch in the nav that flips the stored theme', async () => {
    const user = userEvent.setup()
    const { container } = renderLanding()
    const toggle = screen.getByRole('button', { name: /switch to (dark|light) mode/i })
    expect(container.querySelector('.bl-nav__actions')).toContainElement(toggle)

    await user.click(toggle)
    expect(['light', 'dark']).toContain(useThemeStore.getState().theme)
  })

  it('tabs the example answers between four different layouts', async () => {
    const user = userEvent.setup()
    renderLanding()
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(4)
    const panel = screen.getByRole('tabpanel')

    // Diagnose-a-drop: metric + bars + data table.
    expect(within(panel).getByText('Why did revenue drop last week?')).toBeInTheDocument()
    expect(within(panel).getByText('WoW')).toBeInTheDocument()
    expect(panel.querySelector('.bl-table')).not.toBeNull()

    // Head-to-head comparison: versus cards + share bars, no table.
    await user.click(screen.getByRole('tab', { name: 'Region compare' }))
    expect(within(panel).getByText('Compare sales by region')).toBeInTheDocument()
    expect(within(panel).getByText(/east leads the quarter/i)).toBeInTheDocument()
    expect(within(panel).getByLabelText('Revenue share by region')).toBeInTheDocument()
    expect(panel.querySelector('.bl-table')).toBeNull()

    // Forecast: full trend chart with months and points, no table.
    await user.click(screen.getByRole('tab', { name: 'Forecast' }))
    expect(within(panel).getByText('Forecast next quarter')).toBeInTheDocument()
    expect(within(panel).getByText(/pace suggests/i)).toBeInTheDocument()
    expect(within(panel).getByText('Possible factors')).toBeInTheDocument()
    const svg = panel.querySelector('.bl-spark')
    expect(svg).not.toBeNull()
    const svgText = svg?.textContent ?? ''
    for (const label of ['Aug', 'Oct', 'Jan']) {
      expect(svgText).toContain(label)
    }
    expect(svg?.querySelectorAll('.bl-spark__point').length).toBe(6)
    expect(panel.querySelector('.bl-table')).toBeNull()

    // Channel mix: donut + legend, no table.
    await user.click(screen.getByRole('tab', { name: 'Channel mix' }))
    expect(within(panel).getByText('Where do sales come from?')).toBeInTheDocument()
    expect(within(panel).getByLabelText('Sales by channel')).toBeInTheDocument()
    expect(panel.querySelector('.bl-donut')).not.toBeNull()
    expect(panel.querySelector('.bl-table')).toBeNull()
  })

  it('shows numbers on hover across every example visual', async () => {
    const user = userEvent.setup()
    renderLanding()
    const panel = screen.getByRole('tabpanel')

    // Bars carry week + revenue tips.
    const barTips = [...panel.querySelectorAll('.bl-bar[data-tip]')].map((el) =>
      el.getAttribute('data-tip'),
    )
    expect(barTips).toContain('W3 · $59.1k')

    // Share rows carry region + revenue + share tips.
    await user.click(screen.getByRole('tab', { name: 'Region compare' }))
    const shareTip = panel.querySelector('.bl-share__row[data-tip]')
    expect(shareTip?.getAttribute('data-tip')).toMatch(/East · \$51\.3k · 34%/)

    // Forecast points carry month + value + regime titles.
    await user.click(screen.getByRole('tab', { name: 'Forecast' }))
    const pointTitles = [...panel.querySelectorAll('.bl-spark__point title')].map(
      (el) => el.textContent,
    )
    expect(pointTitles).toContain('Oct · $58.2k · projected')

    // Donut segments carry channel + share + revenue titles.
    await user.click(screen.getByRole('tab', { name: 'Channel mix' }))
    const segTitles = [...panel.querySelectorAll('.bl-donut__seg title')].map(
      (el) => el.textContent,
    )
    expect(segTitles).toContain('Online · 46% · $69.0k')
  })

  it('links the nav to the product sections and auth screens', () => {
    renderLanding()
    const nav = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(nav).getByRole('link', { name: 'Product' })).toHaveAttribute(
      'href',
      '#product',
    )
    expect(within(nav).getByRole('link', { name: 'How it works' })).toHaveAttribute(
      'href',
      '#how',
    )
    expect(within(nav).getByRole('link', { name: 'Trust' })).toHaveAttribute(
      'href',
      '#trust',
    )
    expect(within(nav).getByRole('link', { name: 'Log in' })).toHaveAttribute(
      'href',
      '/signin',
    )
  })
})
