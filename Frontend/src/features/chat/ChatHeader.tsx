/**
 * ChatHeader (F2) — the 56px shell header (specs/14 §3): logo tile, plan
 * badge, theme toggle, "new chat", plus the rail-toggle affordance for the
 * collapsible history rail. The account menu lives in the history-rail
 * footer; the quota chip lives in the composer footer (ambient, next to the
 * scope selector). Styled with the F0 design tokens, wearing the
 * intelligence theme in dark mode (see the `.chat-workspace` scope).
 */
import { LayoutDashboard, Menu, PanelLeft, PanelLeftClose, Plus } from 'lucide-react';
import { Link } from 'react-router-dom';
import { PlanBadge } from '../../components/PlanBadge';
import { ThemeToggle } from '../../components/ThemeToggle';
import { useAuth } from '../../hooks/useAuth';
import { useChatStore } from './chat-store';

interface ChatHeaderProps {
  railOpen: boolean;
  onToggleRail: () => void;
  /** Global AppShell navigation — optional so standalone tests keep working. */
  navOpen?: boolean;
  onToggleNav?: () => void;
}

export function ChatHeader({ railOpen, onToggleRail, navOpen, onToggleNav }: ChatHeaderProps) {
  const { user } = useAuth();
  const newChat = useChatStore((state) => state.newChat);
  const showNavToggle = typeof onToggleNav === 'function';
  const navVisible = navOpen ?? true;

  return (
    <header className="chat-header">
      <div className="chat-header__toggles" role="group" aria-label="Sidebar controls">
        {showNavToggle && (
          <button
            type="button"
            className="chat-header__rail-toggle chat-header__nav-toggle"
            aria-label={navVisible ? 'Hide navigation' : 'Show navigation'}
            aria-expanded={navVisible}
            title={
              navVisible
                ? 'Hide navigation (Ctrl+Shift+B) — more space'
                : 'Show navigation (Ctrl+Shift+B)'
            }
            onClick={onToggleNav}
          >
            {navVisible ? (
              <PanelLeftClose size={18} aria-hidden="true" />
            ) : (
              <LayoutDashboard size={18} aria-hidden="true" />
            )}
          </button>
        )}
        <button
          type="button"
          className="chat-header__rail-toggle"
          aria-label={railOpen ? 'Hide chat history' : 'Show chat history'}
          aria-expanded={railOpen}
          title={
            railOpen
              ? 'Hide chat history (Ctrl+B) — more space'
              : 'Show chat history (Ctrl+B)'
          }
          onClick={onToggleRail}
        >
          {railOpen ? <PanelLeft size={18} /> : <Menu size={18} />}
        </button>
      </div>

      <Link className="chat-header__brand" to="/" aria-label="Buildify Labs home">
        <span className="brand-tile" aria-hidden="true">
          <img src="/logo.png" alt="" />
        </span>
        <span className="chat-header__brand-text">
          <span className="chat-header__brand-name">Buildify Labs</span>
          <span className="chat-header__brand-sub">Intelligence</span>
        </span>
      </Link>

      <span className="chat-header__spacer" />

      {user && <PlanBadge plan={user.plan} />}
      <ThemeToggle />
      <span className="chat-header__divider" aria-hidden="true" />
      <button type="button" className="chat-header__new-chat" onClick={newChat}>
        <Plus size={16} aria-hidden="true" />
        New chat
      </button>
    </header>
  );
}