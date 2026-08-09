/**
 * AuthLayout — the shared shell every auth screen renders inside (F1): a
 * centered token-styled card with the product brand; the active route's screen
 * is the `<Outlet />`. Non-auth routes never render this layout.
 */
import { Outlet } from 'react-router-dom';
import './auth.css';

export function AuthLayout() {
  return (
    <div className="auth-page">
      <main className="auth-card" role="main">
        <div className="auth-brand">
          <p className="auth-brand__name">BuildifyLabs</p>
          <p className="auth-brand__tag">Ask your business data anything</p>
        </div>
        <Outlet />
      </main>
    </div>
  );
}
