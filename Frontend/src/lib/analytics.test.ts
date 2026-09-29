/**
 * analytics (GA4) tests — off by default (no id ⇒ no script, no calls),
 * on with VITE_GA_MEASUREMENT_ID. Analytics must never throw or phone
 * home unconfigured.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetAnalyticsForTests,
  getMeasurementId,
  initAnalytics,
  trackPageview,
} from './analytics'

beforeEach(() => {
  __resetAnalyticsForTests()
  vi.stubEnv('VITE_GA_MEASUREMENT_ID', '')
  document.head
    .querySelectorAll('script[src*="googletagmanager"]')
    .forEach((el) => el.remove())
  delete window.gtag
  delete window.dataLayer
})

describe('analytics', () => {
  it('stays off without a measurement id', () => {
    expect(getMeasurementId()).toBe('')
    expect(initAnalytics()).toBe(false)
    expect(
      document.head.querySelector('script[src*="googletagmanager"]'),
    ).toBeNull()
    expect(() => trackPageview('/')).not.toThrow()
  })

  it('loads gtag once and records page views with an id', () => {
    vi.stubEnv('VITE_GA_MEASUREMENT_ID', 'G-TEST123')
    expect(initAnalytics()).toBe(true)
    const script = document.head.querySelector(
      'script[src*="googletagmanager"]',
    ) as HTMLScriptElement | null
    expect(script?.src).toContain('G-TEST123')

    trackPageview('/signup')
    expect(window.dataLayer).toContainEqual([
      'event',
      'page_view',
      { page_path: '/signup' },
    ])

    // Second init does not inject a duplicate script.
    expect(initAnalytics()).toBe(true)
    expect(
      document.head.querySelectorAll('script[src*="googletagmanager"]'),
    ).toHaveLength(1)
  })
})
