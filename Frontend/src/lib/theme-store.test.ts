/**
 * Theme store — choice persistence, OS resolution, and `<html data-theme>`
 * application. No component rendering; `matchMedia` is absent in jsdom
 * unless stubbed, which the store treats as light (same guard as
 * `useMediaQuery`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  applyTheme,
  resolveTheme,
  toggleThemeValue,
  useThemeStore,
} from './theme-store'

function stubDarkOs(dark: boolean) {
  vi.stubGlobal('matchMedia', () => ({ matches: dark }))
}

beforeEach(() => {
  localStorage.clear()
  useThemeStore.setState({ theme: 'system' })
  document.documentElement.removeAttribute('data-theme')
  vi.unstubAllGlobals()
})

describe('resolveTheme', () => {
  it('returns explicit choices verbatim', () => {
    expect(resolveTheme('light')).toBe('light')
    expect(resolveTheme('dark')).toBe('dark')
  })

  it('resolves system from the OS query', () => {
    stubDarkOs(true)
    expect(resolveTheme('system')).toBe('dark')
    stubDarkOs(false)
    expect(resolveTheme('system')).toBe('light')
  })

  it('treats a missing matchMedia as light', () => {
    expect(resolveTheme('system')).toBe('light')
  })
})

describe('toggleThemeValue', () => {
  it('flips to the opposite of the effective theme', () => {
    expect(toggleThemeValue('light')).toBe('dark')
    expect(toggleThemeValue('dark')).toBe('light')
    stubDarkOs(true)
    expect(toggleThemeValue('system')).toBe('light')
    stubDarkOs(false)
    expect(toggleThemeValue('system')).toBe('dark')
  })
})

describe('applyTheme', () => {
  it('sets the attribute for explicit choices, removes it for system', () => {
    applyTheme('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    applyTheme('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    applyTheme('system')
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })
})

describe('theme store', () => {
  it('defaults to system and persists the choice', () => {
    expect(useThemeStore.getState().theme).toBe('system')
    useThemeStore.getState().setTheme('dark')
    expect(useThemeStore.getState().theme).toBe('dark')
    expect(localStorage.getItem('buildifylabs.theme')).toContain('"dark"')
  })
})
