/**
 * AppShell — the authenticated app frame (Overview / Chat / Data / Reports).
 *
 * Chat keeps its own F2 shell (ChatWorkspace: header + history rail) intact;
 * every other page renders inside this frame: left sidebar on desktop
 * (>=768px), bottom tab bar on narrow viewports. Amber accent only.
 */
import { Database, LayoutDashboard, MessageSquare, Pin } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import { PlanBadge } from '../../components/PlanBadge';
import { ThemeToggle } from '../../components/ThemeToggle';
import { useAuth } from '../../hooks/useAuth';
import { AccountMenu } from '../chat/AccountMenu';
import './app-shell.css';

const NAV = [
  { to: '/app', end: true, label: 'Overview', icon: LayoutDashboard },
  { to: '/app/chat', end: false, label: 'Chat', icon: MessageSquare },
  { to: '/app/data', end: true, label: 'Data', icon: Database },
  { to: '/app/reports', end: true, label: 'Reports', icon: Pin },
] as const;

export function AppShell() {
  const { user } = useAuth();

  return (
    <div className="app-shell">
      <aside className="app-shell__sidebar" aria-label="App navigation">
        <NavLink to="/" className="app-shell__brand" aria-label="Buildify Labs home">
          <span className="brand-tile brand-tile--sm" aria-hidden="true">
            <img src="/logo.png" alt="" />
          </span>
          <span className="app-shell__brand-text">
            <span className="app-shell__brand-name">Buildify Labs</span>
            <span className="app-shell__brand-sub">Intelligence</span>
          </span>
        </NavLink>

        <nav className="app-shell__nav" aria-label="Primary">
          {NAV.map(({ to, end, label, icon: Icon }) => (
            <NavLink
              key={to + label}
              to={to}
              end={end}
              className={({ isActive }) =>
                `app-shell__link${isActive ? ' app-shell__link--active' : ''}`
              }
            >
              <Icon size={17} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="app-shell__sidebar-footer">
          {user && <PlanBadge plan={user.plan} />}
          <AccountMenu align="up" />
        </div>
      </aside>

      <div className="app-shell__main">
        <header className="app-shell__topbar">
          <NavLink to="/" className="app-shell__brand app-shell__brand--top" aria-label="Buildify Labs home">
            <span className="brand-tile brand-tile--sm" aria-hidden="true">
              <img src="/logo.png" alt="" />
            </span>
            <span className="app-shell__brand-name">Buildify Labs</span>
          </NavLink>
          <span className="app-shell__spacer" />
          {user && <PlanBadge plan={user.plan} />}
          <ThemeToggle />
        </header>
        <div className="app-shell__content">
          <Outlet />
        </div>
        <nav className="app-shell__tabbar" aria-label="Primary mobile">
          {NAV.map(({ to, end, label, icon: Icon }) => (
            <NavLink
              key={to + label}
              to={to}
              end={end}
              className={({ isActive }) =>
                `app-shell__tab${isActive ? ' app-shell__tab--active' : ''}`
              }
            >
              <Icon size={19} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}
