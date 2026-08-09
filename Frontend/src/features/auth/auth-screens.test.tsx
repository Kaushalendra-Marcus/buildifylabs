import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from './auth-store'
import { ForgotPasswordScreen } from './ForgotPasswordScreen'
import { ResetPasswordScreen } from './ResetPasswordScreen'
import { SigninScreen } from './SigninScreen'
import { SignupScreen } from './SignupScreen'
import { VerifyEmailScreen } from './VerifyEmailScreen'
import { ApiError } from '../../lib/http'
import type { AuthResponse } from '../../types'

vi.mock('../../api/auth', () => ({
  signup: vi.fn(),
  signin: vi.fn(),
  guest: vi.fn(),
  google: vi.fn(),
  refresh: vi.fn(),
  verifyEmail: vi.fn(),
  forgotPassword: vi.fn(),
  resetPassword: vi.fn(),
}))

import * as authApi from '../../api/auth'

const authResponse: AuthResponse = {
  user: { id: 'user-1', email: 'ada@example.com', name: 'Ada', plan: 'free' },
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  token_type: 'bearer',
}

describe('Auth screens (F1)', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.getState().signOut()
    vi.clearAllMocks()
  })

  describe('SigninScreen', () => {
    it('shows a backend error verbatim (anti-enumeration)', async () => {
      vi.mocked(authApi.signin).mockRejectedValue(
        new ApiError(400, { detail: 'Invalid credentials' }),
      )
      const user = userEvent.setup()

      render(
        <MemoryRouter>
          <SigninScreen />
        </MemoryRouter>,
      )

      await user.type(screen.getByLabelText('Email'), 'ada@example.com')
      await user.type(screen.getByLabelText('Password'), 'wrong-password')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Invalid credentials',
      )
      expect(useAuthStore.getState().status).toBe('unauthenticated')
    })

    it('commits a session on success', async () => {
      vi.mocked(authApi.signin).mockResolvedValue(authResponse)
      const user = userEvent.setup()

      render(
        <MemoryRouter>
          <SigninScreen />
        </MemoryRouter>,
      )

      await user.type(screen.getByLabelText('Email'), 'ada@example.com')
      await user.type(screen.getByLabelText('Password'), 'password123')
      await user.click(screen.getByRole('button', { name: 'Sign in' }))

      await vi.waitFor(() =>
        expect(useAuthStore.getState().status).toBe('authenticated'),
      )
      expect(useAuthStore.getState().user?.plan).toBe('free')
    })

    it('guest sign-in sends a stable persisted device_id', async () => {
      vi.mocked(authApi.guest).mockResolvedValue(authResponse)
      const user = userEvent.setup()

      render(
        <MemoryRouter>
          <SigninScreen />
        </MemoryRouter>,
      )

      const guestButton = screen.getByRole('button', {
        name: 'Continue as guest',
      })
      await user.click(guestButton)
      await vi.waitFor(() =>
        expect(useAuthStore.getState().status).toBe('authenticated'),
      )
      await user.click(guestButton)

      const deviceIds = vi
        .mocked(authApi.guest)
        .mock.calls.map((call) => call[0].device_id)
      expect(deviceIds).toHaveLength(2)
      expect(deviceIds[0]).toBe(deviceIds[1])
      expect(deviceIds[0]).toEqual(expect.any(String))
    })
  })

  describe('SignupScreen', () => {
    it('blocks a password shorter than 8 chars without calling the API', async () => {
      const user = userEvent.setup()

      render(
        <MemoryRouter>
          <SignupScreen />
        </MemoryRouter>,
      )

      await user.type(screen.getByLabelText('Email'), 'ada@example.com')
      await user.type(screen.getByLabelText('Password'), 'short')
      await user.type(screen.getByLabelText('Confirm password'), 'short')
      await user.click(screen.getByRole('button', { name: 'Create account' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'at least 8 characters',
      )
      expect(authApi.signup).not.toHaveBeenCalled()
    })

    it('blocks mismatched confirm password', async () => {
      const user = userEvent.setup()

      render(
        <MemoryRouter>
          <SignupScreen />
        </MemoryRouter>,
      )

      await user.type(screen.getByLabelText('Email'), 'ada@example.com')
      await user.type(screen.getByLabelText('Password'), 'password123')
      await user.type(screen.getByLabelText('Confirm password'), 'different')
      await user.click(screen.getByRole('button', { name: 'Create account' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Passwords do not match',
      )
      expect(authApi.signup).not.toHaveBeenCalled()
    })
  })

  describe('ForgotPasswordScreen', () => {
    it('shows the backend generic message verbatim', async () => {
      vi.mocked(authApi.forgotPassword).mockResolvedValue({
        message: 'If that email is registered, a reset link has been sent.',
      })
      const user = userEvent.setup()

      render(
        <MemoryRouter>
          <ForgotPasswordScreen />
        </MemoryRouter>,
      )

      await user.type(screen.getByLabelText('Email'), 'ada@example.com')
      await user.click(screen.getByRole('button', { name: 'Send reset link' }))

      expect(
        await screen.findByText(
          'If that email is registered, a reset link has been sent.',
        ),
      ).toBeInTheDocument()
      expect(authApi.forgotPassword).toHaveBeenCalledWith({
        email: 'ada@example.com',
      })
    })
  })

  describe('ResetPasswordScreen', () => {
    it('sends the token from the URL with the new password', async () => {
      vi.mocked(authApi.resetPassword).mockResolvedValue({
        message: 'Password reset successfully',
      })
      const user = userEvent.setup()

      render(
        <MemoryRouter initialEntries={['/reset-password?token=tok123']}>
          <ResetPasswordScreen />
        </MemoryRouter>,
      )

      await user.type(screen.getByLabelText('New password'), 'newpass123')
      await user.type(
        screen.getByLabelText('Confirm new password'),
        'newpass123',
      )
      await user.click(screen.getByRole('button', { name: 'Reset password' }))

      expect(
        await screen.findByText('Password reset successfully'),
      ).toBeInTheDocument()
      expect(authApi.resetPassword).toHaveBeenCalledWith({
        token: 'tok123',
        new_password: 'newpass123',
      })
    })

    it('blocks a short password without calling the API', async () => {
      const user = userEvent.setup()

      render(
        <MemoryRouter initialEntries={['/reset-password?token=tok123']}>
          <ResetPasswordScreen />
        </MemoryRouter>,
      )

      await user.type(screen.getByLabelText('New password'), 'short')
      await user.type(screen.getByLabelText('Confirm new password'), 'short')
      await user.click(screen.getByRole('button', { name: 'Reset password' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'at least 8 characters',
      )
      expect(authApi.resetPassword).not.toHaveBeenCalled()
    })
  })

  describe('VerifyEmailScreen', () => {
    it('calls verify-email with the token and shows the message verbatim', async () => {
      vi.mocked(authApi.verifyEmail).mockResolvedValue({
        message: 'Email verified successfully',
      })

      render(
        <MemoryRouter initialEntries={['/verify-email?token=tok123']}>
          <VerifyEmailScreen />
        </MemoryRouter>,
      )

      expect(
        await screen.findByText('Email verified successfully'),
      ).toBeInTheDocument()
      expect(authApi.verifyEmail).toHaveBeenCalledWith('tok123')
    })

    it('shows a backend error verbatim', async () => {
      vi.mocked(authApi.verifyEmail).mockRejectedValue(
        new ApiError(400, { detail: 'Token expired or invalid' }),
      )

      render(
        <MemoryRouter initialEntries={['/verify-email?token=tok123']}>
          <VerifyEmailScreen />
        </MemoryRouter>,
      )

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Token expired or invalid',
      )
    })
  })
})
