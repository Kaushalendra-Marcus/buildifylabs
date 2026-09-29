import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useThemeStore } from '../../lib/theme-store'
import { LandingPage } from './LandingPage'

beforeEach(() => {
  localStorage.clear()
  useThemeStore.setState({ theme: 'system' })
  document.documentElement.removeAttribute('data-theme')
})

afterEach(() => {
  if (hadMatchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: originalMatchMedia,
    })
  } else {
    // @ts-expect-error jsdom has no matchMedia by default — restore that.
    delete window.matchMedia
  }
})

// jsdom ships no matchMedia; the page guards its absence. These tests
// install one per-test and always restore the absence above.
let originalMatchMedia: typeof window.matchMedia | undefined;
let hadMatchMedia = false;

function mockSmallScreen(small: boolean) {
  if (!hadMatchMedia && typeof window.matchMedia === 'function') {
    originalMatchMedia = window.matchMedia;
    hadMatchMedia = true;
  }
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(max-width: 700px)' ? small : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
}

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

  it('tabs the hero demo between four different example layouts', async () => {
    const user = userEvent.setup()
    renderLanding()
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(4)
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
    for (const label of ['$51.3k', '$58.2k', '$66.0k', 'Aug', 'Dec']) {
      expect(svgText).toContain(label)
    }
    expect(svg?.querySelectorAll('.bl-mock__spark-dot').length).toBe(5)
    expect(panel.querySelector('.bl-mock__table')).toBeNull()

    // Channel mix: donut + bars + legend, no table.
    await user.click(screen.getByRole('tab', { name: 'Channel mix' }))
    expect(within(panel).getByText('Where do sales come from?')).toBeInTheDocument()
    expect(within(panel).getByLabelText('Sales by channel')).toBeInTheDocument()
    expect(panel.querySelector('.bl-mock__donut')).not.toBeNull()
    expect(panel.querySelector('.bl-mock__table')).toBeNull()
  })

  it('shows numbers on hover across every demo visual', async () => {
    const user = userEvent.setup()
    renderLanding()
    const panel = screen.getByRole('tabpanel')

    // Bars carry week + revenue tips.
    const barTips = [...panel.querySelectorAll('.bl-mock__bar-col[data-tip]')].map((el) =>
      el.getAttribute('data-tip'),
    )
    expect(barTips).toContain('W3 · $59.1k')

    // Share rows carry region + revenue + share tips.
    await user.click(screen.getByRole('tab', { name: 'Region compare' }))
    const shareTip = panel.querySelector('.bl-mock__share-row[data-tip]')
    expect(shareTip?.getAttribute('data-tip')).toMatch(/East · \$51\.3k · 34%/)

    // Forecast points carry month + value + regime titles.
    await user.click(screen.getByRole('tab', { name: 'Forecast' }))
    const pointTitles = [
      ...panel.querySelectorAll('.bl-mock__point title'),
    ].map((el) => el.textContent)
    expect(pointTitles).toContain('Oct · $58.2k · projected')

    // Donut segments carry channel + share + revenue titles.
    await user.click(screen.getByRole('tab', { name: 'Channel mix' }))
    const segTitles = [
      ...panel.querySelectorAll('.bl-mock__donut-seg title'),
    ].map((el) => el.textContent)
    expect(segTitles).toContain('Online · 46% · $69.0k')
  })

  it('ignores scroll-driving on small screens where the pin runway is static', async () => {
    mockSmallScreen(true)
    const { container } = renderLanding()
    const pin = container.querySelector('.bl-demo-pin') as HTMLElement
    vi.spyOn(pin, 'getBoundingClientRect').mockReturnValue({
      top: -1900,
      height: window.innerHeight + 2000,
    } as DOMRect)
    window.dispatchEvent(new Event('scroll'))
    // Let any rAF-driven update run — the first tab must stay put.
    await waitFor(() =>
      expect(
        screen.getAllByRole('tab')[0],
      ).toHaveAttribute('aria-selected', 'true'),
    )
    expect(screen.getByRole('tab', { name: 'Channel mix' })).toHaveAttribute(
      'aria-selected',
      'false',
    )
    expect(screen.getByText('Tap an example')).toBeInTheDocument()
  })

  it('scroll-drives the demo tabs on wide screens', async () => {
    mockSmallScreen(false)
    const { container } = renderLanding()
    const pin = container.querySelector('.bl-demo-pin') as HTMLElement
    vi.spyOn(pin, 'getBoundingClientRect').mockReturnValue({
      top: -1900,
      height: window.innerHeight + 2000,
    } as DOMRect)
    window.dispatchEvent(new Event('scroll'))
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Channel mix' })).toHaveAttribute(
        'aria-selected',
        'true',
      ),
    )
  })
})
