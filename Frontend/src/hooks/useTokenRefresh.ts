/**
 * useTokenRefresh — re-establishes a session on app load and keeps it alive.
 *
 * Because the access token lives in memory only (F0 decision), a page reload
 * starts with no access token. If a 7-day refresh token is stored, this hook
 * exchanges it for a fresh access token on mount, then re-runs the exchange
 * shortly before the access token would expire (60 min TTL), so an active
 * session never hits a dead token. No stored refresh token ⇒ unauthenticated.
 */
import { useEffect } from 'react';
import * as authApi from '../api/auth';
import { useAuthStore } from '../features/auth/auth-store';
import { tokenStorage } from '../lib/token-storage';

export const ACCESS_TOKEN_TTL_MS = 60 * 60 * 1000; // backend: access 60 min
export const REFRESH_TOKEN_TTL_MS = 7 * 24 * 60 * 60 * 1000; // backend: refresh 7 days

const REFRESH_MARGIN_MS = 60 * 1000; // refresh 1 min before expiry

export function useTokenRefresh(): void {
  useEffect(() => {
    let cancelled = false;

    const refreshToken = tokenStorage.getRefreshToken();
    if (!refreshToken) {
      useAuthStore.getState().setStatus('unauthenticated');
      return;
    }

    const refresh = async (): Promise<void> => {
      try {
        const res = await authApi.refresh({ refresh_token: refreshToken });
        if (!cancelled) useAuthStore.getState().setTokens(res);
      } catch {
        if (!cancelled) useAuthStore.getState().setStatus('unauthenticated');
      }
    };

    void refresh();

    const timer = setInterval(refresh, ACCESS_TOKEN_TTL_MS - REFRESH_MARGIN_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
}
