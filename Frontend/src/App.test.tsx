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

describe('App routing (F1 auth screens)', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.getState().signOut()
  })

  it('renders the sign-in screen when unauthenticated', async () => {
    render(<App />)
    expect(
      await screen.findByRole('heading', { name: 'Sign in' }),
    ).toBeInTheDocument()
  })

  it('guards the workspace: authenticated users land on /app with their plan badge', async () => {
    useAuthStore.getState().setSession({
      user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan: 'free' },
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      token_type: 'bearer',
    })

    render(<App />)
    expect(
      await screen.findByText('The chat workspace lands in F2.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Ada')).toBeInTheDocument()
    expect(screen.getByText('free')).toBeInTheDocument()
  })
})
