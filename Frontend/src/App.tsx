/**
 * App root (F1) — the router (react-router, F0 decision) now serves the
 * public marketing homepage, the auth screens, and the guarded authenticated
 * workspace.
 *
 * Routes:
 * - `/` → the public marketing LandingPage (no guard — same page whether or
 *   not you're signed in; its nav swaps in a "Go to app" action once you are)
 * - `/signin`, `/signup`, `/forgot-password`, `/reset-password`,
 *   `/verify-email` — auth screens (RequireGuest bounces signed-in users away)
 * - `/app` — the Chat Workspace shell (F2)
 *
 * `useTokenRefresh` re-establishes a session from the stored 7-day refresh
 * token on app load (in-memory access token, F0 decision).
 */
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthLayout } from './features/auth/AuthLayout';
import { ForgotPasswordScreen } from './features/auth/ForgotPasswordScreen';
import { ResetPasswordScreen } from './features/auth/ResetPasswordScreen';
import { RequireAuth, RequireGuest } from './features/auth/route-guards';
import { SigninScreen } from './features/auth/SigninScreen';
import { SignupScreen } from './features/auth/SignupScreen';
import { VerifyEmailScreen } from './features/auth/VerifyEmailScreen';
import { ChatWorkspace } from './features/chat/ChatWorkspace';
import { LandingPage } from './features/marketing/LandingPage';
import { useTokenRefresh } from './hooks/useTokenRefresh';

function AppRoutes() {
  useTokenRefresh();

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/app" element={<RequireAuth><ChatWorkspace /></RequireAuth>} />
      <Route element={<RequireGuest><AuthLayout /></RequireGuest>}>
        <Route path="/signin" element={<SigninScreen />} />
        <Route path="/signup" element={<SignupScreen />} />
        <Route path="/forgot-password" element={<ForgotPasswordScreen />} />
        <Route path="/reset-password" element={<ResetPasswordScreen />} />
        <Route path="/verify-email" element={<VerifyEmailScreen />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
