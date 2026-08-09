/**
 * Token storage decision (F0): the backend expects a **Bearer header**, so a
 * cookie-backed approach would need a backend change (docs/type-contracts.md
 * §Auth). Between the remaining options we keep the **access token in memory
 * only** (never localStorage — an XSS-read vector for a product handling
 * business data) and persist the **7-day refresh token** to localStorage so a
 * page reload can rehydrate the session. `useTokenRefresh` (src/hooks) turns
 * the stored refresh token back into an in-memory access token on app load.
 */

const REFRESH_KEY = 'buildifylabs.refresh_token';

let accessToken: string | null = null;

function persistRefresh(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(REFRESH_KEY, token);
    } else {
      localStorage.removeItem(REFRESH_KEY);
    }
  } catch {
    // Storage unavailable (e.g. privacy mode) — the session just won't survive
    // a reload; everything else still works.
  }
}

export const tokenStorage = {
  getAccessToken: (): string | null => accessToken,
  setAccessToken: (token: string | null): void => {
    accessToken = token;
  },
  getRefreshToken: (): string | null => {
    try {
      return localStorage.getItem(REFRESH_KEY);
    } catch {
      return null;
    }
  },
  setRefreshToken: (token: string | null): void => persistRefresh(token),
  setTokens: (access: string | null, refresh: string | null): void => {
    accessToken = access;
    persistRefresh(refresh);
  },
  clear: (): void => {
    accessToken = null;
    persistRefresh(null);
  },
};
