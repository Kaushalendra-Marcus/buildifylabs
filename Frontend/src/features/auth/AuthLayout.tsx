/**
 * AuthLayout — the shared shell every auth screen renders inside (F1):
 * illustration left (`public/login.png`, framed on the fox + books with the
 * empty right-side scenery cropped away), form card right (brand + the active
 * route's `<Outlet />`). The art panel collapses away below 900px, so narrow
 * viewports get the form alone. Non-auth routes never render this layout.
 */
import { Outlet } from 'react-router-dom';
import './auth.css';

export function AuthLayout() {
  return (
    <div className="auth-page">
      <div className="auth-split">
        <aside className="auth-art" aria-label="BuildifyLabs illustration">
          <img
            className="auth-art__image"
            src="/login.png"
            alt="BuildifyLabs fox working on a laptop beside books labelled Graph, Sales and Statics"
          />
          <div className="auth-art__caption">
            <p className="auth-art__title">Your business, explained</p>
            <ul className="auth-art__points">
              <li>Upload a CSV, PDF, or spreadsheet</li>
              <li>Ask questions in plain English</li>
              <li>Get charts, root causes, and next steps</li>
            </ul>
          </div>
        </aside>
        <main className="auth-card" role="main">
          <div className="auth-brand">
            <span className="brand-tile" aria-hidden="true">
              <img src="/logo.png" alt="" />
            </span>
            <p className="auth-brand__name">BuildifyLabs</p>
            <p className="auth-brand__tag">Ask your business data anything</p>
          </div>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
