import { beforeEach, describe, expect, it } from 'vitest'
import { tokenStorage } from './token-storage'

describe('tokenStorage (F0 token decision)', () => {
  beforeEach(() => {
    tokenStorage.clear()
    localStorage.clear()
  })

  it('keeps the access token in memory only (never localStorage)', () => {
    tokenStorage.setAccessToken('access-123')
    expect(tokenStorage.getAccessToken()).toBe('access-123')
    expect(localStorage.getItem('buildifylabs.access_token')).toBeNull()
  })

  it('persists the refresh token to localStorage', () => {
    tokenStorage.setRefreshToken('refresh-123')
    expect(tokenStorage.getRefreshToken()).toBe('refresh-123')
    expect(localStorage.getItem('buildifylabs.refresh_token')).toBe('refresh-123')
  })

  it('setTokens writes both, clear wipes both', () => {
    tokenStorage.setTokens('access-1', 'refresh-1')
    expect(tokenStorage.getAccessToken()).toBe('access-1')
    expect(tokenStorage.getRefreshToken()).toBe('refresh-1')

    tokenStorage.clear()
    expect(tokenStorage.getAccessToken()).toBeNull()
    expect(tokenStorage.getRefreshToken()).toBeNull()
  })
})
