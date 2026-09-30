/**
 * AppShell — the authenticated app frame (Overview / Chat / Data / Reports).
 *
 * Chat keeps its own F2 shell (ChatWorkspace: header + history rail) intact;
 * every other page renders inside this frame: left sidebar on desktop
 * (>=768px), bottom tab bar on narrow viewports. Amber accent only.
 */
import { Activity, Database, LayoutDashboard, MessageSquare, PanelLeftClose, PanelLeftOpen, Pin, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { PlanBadge } from '../../components/PlanBadge';
import { ThemeToggle } from '../../components/ThemeToggle';
import { useAuth } from '../../hooks/useAuth';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { AccountMenu } from '../chat/AccountMenu';
import { CommandPalette } from './CommandPalette';
import { useShellStore } from './shell-store';
import './app-shell.css';

const NAV = [
  { to: '/app', end: true, label: 'Overview', icon: LayoutDashboard },
  { to: '/app/chat', end: false, label: 'Chat', icon: MessageSquare },
  { to: '/app/data', end: true, label: 'Data', icon: Database },
  { to: '/app/reports', end: true, label: 'Reports', icon: Pin },
  { to: '/app/activity', end: true, label: 'Activity', icon: Activity },
] as const;

export function AppShell() {
  const { user } = useAuth();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const navOpen = useShellStore((state) => state.navOpen);
  const setNavOpen = useShellStore((state) => state.setNavOpen);
  const toggleNav = useShellStore((state) => state.toggleNav);
  const location = useLocation();
  const isChatRoute = location.pathname.startsWith('/app/chat');
  // On phones the chat route hides the topbar + tab bar (single-header,
  // full-height chat); the sidebar becomes a left overlay drawer there.
  const isNarrow = useMediaQuery('(max-width: 767.98px)');
  const showNavDrawer = isChatRoute && isNarrow && navOpen;
  const closeNav = () => {
    if (isNarrow) setNavOpen(false);
  };

  // Global quick switcher: Ctrl/⌘+K toggles from anywhere in the app.
  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((value) => !value);
      }
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, []);

  // Modern rail shortcut: Ctrl/⌘+Shift+B toggles the global navigation from
  // anywhere (mirrors the chat header toggle). Ignored while typing so we
  // never steal keystrokes from the composer.
  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target != null &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable);
      if (typing) return;
      if (
        (event.ctrlKey || event.metaKey) &&
        event.shiftKey &&
        event.key.toLowerCase() === 'b'
      ) {
        event.preventDefault();
        toggleNav();
      }
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [toggleNav]);

  return (
    <div
      className={`app-shell${navOpen ? '' : ' app-shell--nav-collapsed'}${isChatRoute ? ' app-shell--on-chat' : ''}`}
    >
      <aside
        className="app-shell__sidebar"
        aria-label="App navigation"
        aria-hidden={navOpen ? undefined : true}
        inert={!navOpen}
      >
        <div className="app-shell__sidebar-head">
          <NavLink to="/" className="app-shell__brand" aria-label="Buildify Labs home">
            <span className="brand-tile brand-tile--sm" aria-hidden="true">
              <img src="/logo.png" alt="" />
            </span>
            <span className="app-shell__brand-text">
              <span className="app-shell__brand-name">Buildify Labs</span>
              <span className="app-shell__brand-sub">Intelligence</span>
            </span>
          </NavLink>
          <button
            type="button"
            className="app-shell__collapse-btn"
            onClick={toggleNav}
            aria-label="Hide navigation"
            title="Hide navigation (Ctrl+Shift+B) — more space for content"
          >
            <PanelLeftClose size={16} aria-hidden="true" />
          </button>
        </div>

        <nav className="app-shell__nav" aria-label="Primary">
          {NAV.map(({ to, end, label, icon: Icon }) => (
            <NavLink
              key={to + label}
              to={to}
              end={end}
              onClick={closeNav}
              className={({ isActive }) =>
                `app-shell__link${isActive ? ' app-shell__link--active' : ''}`
              }
            >
              <Icon size={17} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <button
          type="button"
          className="app-shell__palette-trigger"
          onClick={() => setPaletteOpen(true)}
          aria-label="Open quick switcher"
          title="Quick switcher (Ctrl+K)"
        >
          <Search size={15} aria-hidden="true" />
          <span>Quick switch…</span>
          <kbd>⌘K</kbd>
        </button>

        <div className="app-shell__sidebar-footer">
          {user && <PlanBadge plan={user.plan} />}
          <AccountMenu align="up" />
        </div>
      </aside>

      <div className="app-shell__main">
        {showNavDrawer && (
          <button
            type="button"
            className="app-shell__nav-backdrop"
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
          />
        )}
        {!navOpen && (
          <button
            type="button"
            className="app-shell__expand-fab"
            onClick={toggleNav}
            aria-label="Show navigation"
            title="Show navigation (Ctrl+Shift+B)"
          >
            <PanelLeftOpen size={16} aria-hidden="true" />
            <span>Menu</span>
          </button>
        )}
        <header className="app-shell__topbar">
          <NavLink to="/" className="app-shell__brand app-shell__brand--top" aria-label="Buildify Labs home">
            <span className="brand-tile brand-tile--sm" aria-hidden="true">
              <img src="/logo.png" alt="" />
            </span>
            <span className="app-shell__brand-name">Buildify Labs</span>
          </NavLink>
          <span className="app-shell__spacer" />
          {user && <PlanBadge plan={user.plan} />}
          <button
            type="button"
            className="app-shell__palette-trigger app-shell__palette-trigger--top"
            onClick={() => setPaletteOpen(true)}
            aria-label="Open quick switcher"
          >
            <Search size={16} aria-hidden="true" />
          </button>
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
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
