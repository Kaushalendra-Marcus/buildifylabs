/**
 * ChatHeader (F2) — the 56px shell header (specs/14 §3): logo, quota chip,
 * plan badge, account menu, "new chat", plus the rail-toggle affordance for
 * the collapsible history rail. Styled with the F0 design tokens.
 */
import { Menu, PanelLeft, Plus, Sparkles } from 'lucide-react';
import { PlanBadge } from '../../components/PlanBadge';
import { useAuth } from '../../hooks/useAuth';
import { AccountMenu } from './AccountMenu';
import { QuotaChip } from './QuotaChip';

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
        <Sparkles className="chat-header__brand-icon" size={18} aria-hidden="true" />
        BuildifyLabs
      </span>

      <span className="chat-header__spacer" />

      <QuotaChip />
      {user && <PlanBadge plan={user.plan} />}
      <AccountMenu />
      <button type="button" className="chat-header__new-chat">
        <Plus size={16} aria-hidden="true" />
        New chat
      </button>
    </header>
  );
}