/**
 * ChatHeader (F2) — the 56px shell header (specs/14 §3): logo tile, plan
 * badge, account menu, "new chat", plus the rail-toggle affordance for the
 * collapsible history rail. The quota chip lives in the composer footer
 * (ambient, next to the scope selector). Styled with the F0 design tokens,
 * forced to the dark intelligence theme by the `.chat-workspace` scope.
 */
import { Menu, PanelLeft, Plus } from 'lucide-react';
import { PlanBadge } from '../../components/PlanBadge';
import { useAuth } from '../../hooks/useAuth';
import { AccountMenu } from './AccountMenu';

interface ChatHeaderProps {
  railOpen: boolean;
  onToggleRail: () => void;
}

export function ChatHeader({ railOpen, onToggleRail }: ChatHeaderProps) {
  const { user } = useAuth();

  return (
    <header className="chat-header">
      <button
        type="button"
        className="chat-header__rail-toggle"
        aria-label={railOpen ? 'Hide chat history' : 'Show chat history'}
        aria-expanded={railOpen}
        onClick={onToggleRail}
      >
        {railOpen ? <PanelLeft size={18} /> : <Menu size={18} />}
      </button>

      <span className="chat-header__brand">
        <span className="brand-tile" aria-hidden="true">
          <img src="/logo.png" alt="" />
        </span>
        <span className="chat-header__brand-text">
          <span className="chat-header__brand-name">Buildify Labs</span>
          <span className="chat-header__brand-sub">Intelligence</span>
        </span>
      </span>

      <span className="chat-header__spacer" />

      {user && <PlanBadge plan={user.plan} />}
      <span className="chat-header__divider" aria-hidden="true" />
      <button type="button" className="chat-header__new-chat">
        <Plus size={16} aria-hidden="true" />
        New chat
      </button>
      <AccountMenu />
    </header>
  );
}