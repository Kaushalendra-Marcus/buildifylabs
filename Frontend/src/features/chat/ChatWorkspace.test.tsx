/**
 * ChatWorkspace F2 shell tests — specs/14 §3 acceptance: three regions, one
 * layout (header / rail / message stream + composer); rail collapsed by
 * default below 768px and overlaid (never pushes content) on narrow viewports.
 *
 * matchMedia is stubbed per-test to fake the desktop / narrow breakpoints
 * since jsdom does not implement it.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatWorkspace } from './ChatWorkspace'
import { useAuthStore } from '../auth/auth-store'

/** The header brand is a router Link home — every render needs the context. */
function renderWorkspace() {
  return render(
    <MemoryRouter>
      <ChatWorkspace />
    </MemoryRouter>,
  )
}

const NARROW_QUERY = '(max-width: 767.98px)'

function stubMatchMedia(narrow: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query === NARROW_QUERY ? narrow : !narrow,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

function signedIn() {
  useAuthStore.getState().setSession({
    user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan: 'free' },
    access_token: 'access-1',
    refresh_token: 'refresh-1',
    token_type: 'bearer',
  })
}

describe('ChatWorkspace shell (F2)', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.getState().signOut()
    signedIn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the header, rail, stream and composer regions in one layout', () => {
    stubMatchMedia(false) // desktop >=768px → rail open by default
    renderWorkspace()

    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide chat history' })).toBeInTheDocument()
    expect(
      screen.getByRole('complementary', { name: 'Chat history' }),
    ).toHaveClass('history-rail--open')
    expect(screen.getByRole('region', { name: 'Message stream' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Composer' })).toBeInTheDocument()
  })

  it('links the header brand to the home page', () => {
    stubMatchMedia(false)
    renderWorkspace()

    expect(screen.getByRole('link', { name: 'Buildify Labs home' })).toHaveAttribute(
      'href',
      '/',
    )
  })

  it('brands the header with the logo tile and Intelligence subtitle', () => {
    stubMatchMedia(false)
    const { container } = renderWorkspace()

    expect(screen.getByText('Buildify Labs')).toBeInTheDocument()
    expect(screen.getByText('Intelligence')).toBeInTheDocument()
    const tile = container.querySelector('.chat-header .brand-tile img')
    expect(tile?.getAttribute('src')).toBe('/logo.png')
  })

  it('keeps the quota chip in the composer footer, next to the scope selector', () => {
    stubMatchMedia(false)
    renderWorkspace()

    const composer = screen.getByRole('region', { name: 'Composer' })
    expect(composer.querySelector('.composer__top')).toBeInTheDocument()
    expect(composer.querySelector('.quota-chip')).toBeInTheDocument()
  })

  it('collapses the rail by default on narrow viewports (overlay, not pushed)', () => {
    stubMatchMedia(true) // <768px
    renderWorkspace()

    expect(screen.getByRole('complementary', { name: 'Chat history' })).toHaveClass(
      'history-rail--closed',
    )
    expect(screen.getByRole('button', { name: 'Show chat history' })).toBeInTheDocument()
  })

  it('opens the rail as an overlay via the header toggle on narrow viewports', async () => {
    stubMatchMedia(true)
    const user = userEvent.setup()
    renderWorkspace()

    await user.click(screen.getByRole('button', { name: 'Show chat history' }))

    expect(screen.getByRole('complementary', { name: 'Chat history' })).toHaveClass(
      'history-rail--open',
    )
  })
})