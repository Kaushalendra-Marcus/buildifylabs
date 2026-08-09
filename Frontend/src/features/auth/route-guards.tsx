/**
 * Route guards (F1). Auth status is driven by the Zustand auth store
 * (features/auth/auth-store.ts):
 *
 * - `idle` — nothing known yet (app load; useTokenRefresh still resolving) → a
 *   token-styled loading screen, never a premature redirect.
 * - `loading` — an auth action is in flight (useAuth sets it) → the form stays
 *   visible so the error can surface in place.
 * - `authenticated` / `unauthenticated` → redirect as documented below.
 */
import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuthStore } from './auth-store';
import './route-guards.css';

function RouteLoading() {
  return (
    <div className="route-loading" role="status" aria-label="Loading">
      <span className="route-loading__spinner" aria-hidden="true" />
      Loading…
    </div>
  );
}

/** Protects the authenticated workspace: redirects to /signin when signed out. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);

  if (status === 'authenticated') return children;
  if (status === 'idle' || status === 'loading') return <RouteLoading />;
  return <Navigate to="/signin" replace />;
}

/** Wraps the auth screens: signed-in users bounce straight to the workspace. */
export function RequireGuest({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);

  if (status === 'authenticated') return <Navigate to="/app" replace />;
  if (status === 'idle' || status === 'loading') return <RouteLoading />;
  return children;
}
