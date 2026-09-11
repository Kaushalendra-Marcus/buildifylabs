/**
 * ThemeToggle — shows the mode a press moves TO, flips the stored choice,
 * and reflects it on `<html data-theme>` (via the App effect in prod; the
 * store subscription here drives the icon).
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { applyTheme, useThemeStore } from '../lib/theme-store'
import { ThemeToggle } from './ThemeToggle'

beforeEach(() => {
  localStorage.clear()
  useThemeStore.setState({ theme: 'system' })
  document.documentElement.removeAttribute('data-theme')
})

// Mirror of the App effect so the test proves the attribute contract.
useThemeStore.subscribe((state) => applyTheme(state.theme))

describe('ThemeToggle', () => {
  it('offers dark mode while the effective theme is light', () => {
    render(<ThemeToggle />)
    expect(
      screen.getByRole('button', { name: 'Switch to dark mode' }),
    ).toBeInTheDocument()
  })

  it('clicking switches to dark and back to light', async () => {
    const user = userEvent.setup()
    render(<ThemeToggle />)

    await user.click(screen.getByRole('button', { name: 'Switch to dark mode' }))
    expect(useThemeStore.getState().theme).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(
      screen.getByRole('button', { name: 'Switch to light mode' }),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Switch to light mode' }))
    expect(useThemeStore.getState().theme).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })
})
