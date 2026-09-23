/**
 * App root (F1) — the router (react-router, F0 decision) now serves the
 * the auth screens and the guarded authenticated
 * workspace.
 *
 * Routes:
 * - `/` — the public landing page (no guard; links into /signin, /signup, /app)
 * - `/signin`, `/signup`, `/forgot-password`, `/reset-password`,
 *   `/verify-email` — auth screens (RequireGuest bounces signed-in users away)
 * - `/app` — the authenticated AppShell (Overview index by default)
 * - `/app/chat` — the Chat Workspace shell (F2, history rail + stream)
 * - `/app/data` — Datasets page (upload + file list)
 * - `/app/reports` — Pinned reports (pin from any chat answer)
 * - `/app/activity` — Usage quotas + conversation history
 *
 * `useTokenRefresh` re-establishes a session from the stored 7-day refresh
 * token on app load (in-memory access token, F0 decision).
 */
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { useEffect } from 'react';
import { AuthLayout } from './features/auth/AuthLayout';
import { ForgotPasswordScreen } from './features/auth/ForgotPasswordScreen';
import { ResetPasswordScreen } from './features/auth/ResetPasswordScreen';
import { RequireAuth, RequireGuest } from './features/auth/route-guards';
import { SigninScreen } from './features/auth/SigninScreen';
import { SignupScreen } from './features/auth/SignupScreen';
import { VerifyEmailScreen } from './features/auth/VerifyEmailScreen';
import { ActivityPage } from './features/activity/ActivityPage';
import { ChatWorkspace } from './features/chat/ChatWorkspace';
import { DataPage } from './features/data/DataPage';
import { LandingPage } from './features/landing/LandingPage';
import { OverviewPage } from './features/overview/OverviewPage';
import { ReportsPage } from './features/reports/ReportsPage';
import { AppShell } from './features/shell/AppShell';
import { useTokenRefresh } from './hooks/useTokenRefresh';
import { applyTheme, useThemeStore } from './lib/theme-store';

function AppRoutes() {
  useTokenRefresh();
  const theme = useThemeStore((state) => state.theme);

  // Whole-app theme: reflect the stored choice onto `<html data-theme>`.
  // `system` stores no attribute, so the CSS `prefers-color-scheme`
  // fallback (plus the index.html pre-paint guard) tracks the OS alone.
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/app" element={<RequireAuth><AppShell /></RequireAuth>}>
        <Route index element={<OverviewPage />} />
        <Route path="chat" element={<ChatWorkspace />} />
        <Route path="data" element={<DataPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="activity" element={<ActivityPage />} />
      </Route>
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
