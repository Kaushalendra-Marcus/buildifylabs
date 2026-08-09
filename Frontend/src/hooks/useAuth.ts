/**
 * useAuth — the app's single auth surface for screens. Wraps the auth store
 * (Zustand) and the live `src/api/auth.ts` seam. The sign-in helpers set
 * `status: "loading"`, then commit a session (or let the thrown `ApiError`
 * propagate so screens can display the backend's generic message verbatim —
 * anti-enumeration, specs/01). On failure the store status is reset to
 * `unauthenticated` so the auth screens (and their route guard) never stay
 * stuck on the loading state.
 */
import { useCallback } from 'react';
import * as authApi from '../api/auth';
import { useAuthStore } from '../features/auth/auth-store';
import type {
  GoogleRequest,
  GuestRequest,
  SigninRequest,
  SignupRequest,
} from '../types';

export function useAuth() {
  const user = useAuthStore((s) => s.user);
  const accessToken = useAuthStore((s) => s.accessToken);
  const status = useAuthStore((s) => s.status);

  const signup = useCallback(async (body: SignupRequest) => {
    useAuthStore.getState().setStatus('loading');
    try {
      const res = await authApi.signup(body);
      useAuthStore.getState().setSession(res);
    } catch (error) {
      useAuthStore.getState().setStatus('unauthenticated');
      throw error;
    }
  }, []);

  const signin = useCallback(async (body: SigninRequest) => {
    useAuthStore.getState().setStatus('loading');
    try {
      const res = await authApi.signin(body);
      useAuthStore.getState().setSession(res);
    } catch (error) {
      useAuthStore.getState().setStatus('unauthenticated');
      throw error;
    }
  }, []);

  const signInAsGuest = useCallback(async (body: GuestRequest) => {
    useAuthStore.getState().setStatus('loading');
    try {
      const res = await authApi.guest(body);
      useAuthStore.getState().setSession(res);
    } catch (error) {
      useAuthStore.getState().setStatus('unauthenticated');
      throw error;
    }
  }, []);

  const signInWithGoogle = useCallback(async (body: GoogleRequest) => {
    useAuthStore.getState().setStatus('loading');
    try {
      const res = await authApi.google(body);
      useAuthStore.getState().setSession(res);
    } catch (error) {
      useAuthStore.getState().setStatus('unauthenticated');
      throw error;
    }
  }, []);

  const signOut = useCallback(() => {
    useAuthStore.getState().signOut();
  }, []);

  return {
    user,
    accessToken,
    status,
    isAuthenticated: status === 'authenticated',
    signup,
    signin,
    signInAsGuest,
    signInWithGoogle,
    signOut,
  };
}
