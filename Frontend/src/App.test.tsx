import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { useAuthStore } from './features/auth/auth-store'

// Mock the auth API seam so useTokenRefresh never hits a real backend during
// tests: refresh resolves to a fresh pair, keeping an established session alive.
vi.mock('./api/auth', () => ({
  signup: vi.fn(),
  signin: vi.fn(),
  guest: vi.fn(),
  google: vi.fn(),
  refresh: vi.fn().mockResolvedValue({
    access_token: 'access-2',
    refresh_token: 'refresh-2',
    token_type: 'bearer',
  }),
  verifyEmail: vi.fn(),
  forgotPassword: vi.fn(),
  resetPassword: vi.fn(),
}))

describe('App routing (F1 auth screens + landing)', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    window.history.pushState({}, '', '/')
    useAuthStore.getState().signOut()
  })

  it('renders the public landing page at /', async () => {
    render(<App />)
    expect(
      await screen.findByRole('heading', { name: /ask your business data anything/i }),
    ).toBeInTheDocument()
    // Landing links into the real routes — never a redirect to /app.
    const loginLinks = screen.getAllByRole('link', { name: 'Log in' });
    expect(loginLinks.length).toBeGreaterThan(0);
    loginLinks.forEach((link) => expect(link).toHaveAttribute('href', '/signin'));
    expect(screen.getByRole('textbox', { name: 'Ask a business question' })).toBeInTheDocument()
  })

  it('renders the sign-in screen at /signin when unauthenticated', async () => {
    window.history.pushState({}, '', '/signin')
    render(<App />)
    expect(
      await screen.findByRole('heading', { name: 'Sign in' }),
    ).toBeInTheDocument()
  })

  it('guards the workspace: authenticated users land on the /app Chat Workspace shell', async () => {
    window.history.pushState({}, '', '/app')
    useAuthStore.getState().setSession({
      user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan: 'free' },
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      token_type: 'bearer',
    })

    render(<App />)
    // F2 shell: the Chat Workspace replaces the F1 Workspace placeholder.
    expect(
      await screen.findByRole('button', { name: 'New chat' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Chat history' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Message stream' })).toBeInTheDocument()
    expect(screen.getByText('Ada')).toBeInTheDocument()
    expect(screen.getByText('free')).toBeInTheDocument()
  })
})
