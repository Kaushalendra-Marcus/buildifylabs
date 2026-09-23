/**
 * CommandPalette — Ctrl/⌘+K quick switcher for the authenticated app.
 * One modal, no dependencies: type to filter, Enter runs the first match,
 * Esc closes. Actions are navigation (the five shell pages), New chat,
 * quick-ask templates (same sessionStorage handoff the Overview uses) and
 * a theme toggle. Mounted once by AppShell.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity,
  Database,
  LayoutDashboard,
  MessageSquare,
  Moon,
  Pin,
  Plus,
  Sparkles,
  UploadCloud,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useThemeStore, toggleThemeValue } from '../../lib/theme-store';
import { useChatStore } from '../chat/chat-store';
import './command-palette.css';

const PENDING_KEY = 'bl-pending-question';

const QUICK_ASKS = [
  'Why did revenue drop last week?',
  'Show month-over-month growth',
  'Top 5 products by revenue',
  'Forecast next quarter',
];

interface PaletteAction {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  run: () => void;
}

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  // Fresh mount per opening (instead of clearing state in an effect), so the
  // query always starts empty — lint-clean under react-hooks/set-state-in-effect.
  if (!open) return null;
  return <PaletteDialog onClose={onClose} />;
}

function PaletteDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');

  const actions = useMemo<PaletteAction[]>(
    () => [
      {
        id: 'nav-overview',
        group: 'Go to',
        label: 'Overview dashboard',
        icon: LayoutDashboard,
        run: () => navigate('/app'),
      },
      {
        id: 'nav-chat',
        group: 'Go to',
        label: 'Chat',
        icon: MessageSquare,
        run: () => navigate('/app/chat'),
      },
      {
        id: 'nav-data',
        group: 'Go to',
        label: 'Datasets',
        icon: Database,
        run: () => navigate('/app/data'),
      },
      {
        id: 'nav-reports',
        group: 'Go to',
        label: 'Pinned reports',
        icon: Pin,
        run: () => navigate('/app/reports'),
      },
      {
        id: 'nav-activity',
        group: 'Go to',
        label: 'Activity & usage',
        icon: Activity,
        run: () => navigate('/app/activity'),
      },
      {
        id: 'new-chat',
        group: 'Actions',
        label: 'New chat',
        icon: Plus,
        run: () => {
          useChatStore.getState().newChat();
          navigate('/app/chat');
        },
      },
      {
        id: 'upload',
        group: 'Actions',
        label: 'Upload data',
        icon: UploadCloud,
        run: () => navigate('/app/data'),
      },
      {
        id: 'theme',
        group: 'Actions',
        label: 'Toggle theme',
        icon: Moon,
        run: () => {
          const current = useThemeStore.getState().theme;
          useThemeStore.getState().setTheme(toggleThemeValue(current));
        },
      },
      ...QUICK_ASKS.map((question, index) => ({
        id: `ask-${index}`,
        group: 'Ask',
        label: question,
        hint: 'Ask in chat',
        icon: Sparkles,
        run: () => {
          try {
            sessionStorage.setItem(PENDING_KEY, question);
          } catch {
            /* storage unavailable — chat still opens */
          }
          navigate('/app/chat');
        },
      })),
    ],
    [navigate],
  );

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    return actions.filter(
      (action) =>
        action.label.toLowerCase().includes(q) ||
        action.group.toLowerCase().includes(q),
    );
  }, [actions, query]);

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  function runAction(action: PaletteAction) {
    action.run();
    onClose();
  }

  return (
    <div className="palette-layer">
      <button
        type="button"
        className="palette-backdrop"
        aria-label="Close quick switcher"
        onClick={onClose}
      />
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Quick switcher"
      >
        <input
          autoFocus
          className="palette__input"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && matches.length > 0) {
              runAction(matches[0]);
            }
          }}
          placeholder="Go to a page, start a chat, ask a question…"
          aria-label="Search actions"
        />
        <ul className="palette__list" role="listbox" aria-label="Actions">
          {matches.length === 0 && (
            <li className="palette__empty">No matching actions.</li>
          )}
          {matches.map((action) => (
            <li key={action.id}>
              <button
                type="button"
                className="palette__item"
                role="option"
                aria-selected="false"
                onClick={() => runAction(action)}
              >
                <action.icon size={16} aria-hidden="true" />
                <span className="palette__item-label">{action.label}</span>
                <span className="palette__item-group">{action.group}</span>
              </button>
            </li>
          ))}
        </ul>
        <p className="palette__hint">Enter runs the first match · Esc closes</p>
      </div>
    </div>
  );
}
