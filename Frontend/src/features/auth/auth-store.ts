/**
 * Auth session store (Zustand). Token persistence is delegated to
 * `src/lib/token-storage.ts` (F0 decision: access token in memory, refresh
 * token in localStorage). This store is the reactive mirror for React consumers.
 */
import { create } from 'zustand';
import { tokenStorage } from '../../lib/token-storage';
import type { AuthResponse, AuthUserResponse, TokenResponse } from '../../types';

export type AuthStatus =
  | 'idle' // nothing known yet (app load)
  | 'loading' // an auth request is in flight
  | 'authenticated'
  | 'unauthenticated';

interface AuthState {
  user: AuthUserResponse | null;
  accessToken: string | null;
  status: AuthStatus;
  setStatus(status: AuthStatus): void;
  /** Persist + activate a full AuthResponse (signup/signin/guest/google). */
  setSession(auth: AuthResponse): void;
  /** Persist + activate a refresh response (no `user` field). */
  setTokens(tokens: TokenResponse): void;
  setUser(user: AuthUserResponse): void;
  signOut(): void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  status: 'idle',

  setStatus: (status) => set({ status }),

  setSession: (auth) => {
    tokenStorage.setTokens(auth.access_token, auth.refresh_token ?? null);
    set({
      user: auth.user,
      accessToken: auth.access_token,
      status: 'authenticated',
    });
  },

  setTokens: (tokens) => {
    tokenStorage.setTokens(tokens.access_token, tokens.refresh_token ?? null);
    set({
      accessToken: tokens.access_token,
      status: tokens.access_token ? 'authenticated' : 'unauthenticated',
    });
  },

  setUser: (user) => set({ user }),

  signOut: () => {
    tokenStorage.clear();
    set({ user: null, accessToken: null, status: 'unauthenticated' });
  },
}));
