/**
 * ChatWorkspace (F2) — the Chat Workspace shell, the single principal screen
 * of the product (specs/14 §3).
 *
 * Three regions in ONE layout — no separate drawer/workspace surface:
 *   - Header (56px): logo, quota chip, plan badge, account menu, "new chat"
 *   - Chat history rail: 0 / 280px, collapsed by default below 768px, and an
 *     OVERLAY on narrow viewports (it never pushes the stream column)
 *   - Message stream (the only place visuals render — F3 fills it)
 *   - Composer fixed to the bottom of the stream column (F5 fills it)
 *
 * Constraint honour begins here: NO resizable panel, NO fullscreen affordance.
 */
import { useCallback, useEffect, useState } from 'react';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { useShellStore } from '../shell/shell-store';
import { ChatHeader } from './ChatHeader';
import { HistoryRail } from './HistoryRail';
import { MessageStream } from './MessageStream';
import { Composer } from './Composer';
import './chat-workspace.css';

const NARROW_MAX_QUERY = '(max-width: 767.98px)';
const RAIL_STORAGE_KEY = 'bl-rail-open';

function initialRailOpen(): boolean {
  try {
    const stored = localStorage.getItem(RAIL_STORAGE_KEY);
    if (stored === '1') return true;
    if (stored === '0') return false;
  } catch {
    /* storage unavailable */
  }
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true;
  }
  return !window.matchMedia(NARROW_MAX_QUERY).matches;
}

export function ChatWorkspace() {
  const isNarrow = useMediaQuery(NARROW_MAX_QUERY);
  // Rail defaults: open on desktop (>=768px), collapsed by default <768px
  // (specs/14 §3). Only the initial value follows the viewport — the toggle is
  // the user's after that, persisted so a refresh keeps their space choice.
  const [railOpen, setRailOpen] = useState<boolean>(initialRailOpen);
  const navOpen = useShellStore((state) => state.navOpen);
  const toggleNav = useShellStore((state) => state.toggleNav);

  const toggleRail = useCallback(() => {
    setRailOpen((open) => {
      const next = !open;
      try {
        localStorage.setItem(RAIL_STORAGE_KEY, next ? '1' : '0');
      } catch {
        /* storage unavailable — toggle still works for the session */
      }
      return next;
    });
  }, []);

  const closeRail = useCallback(() => {
    try {
      localStorage.setItem(RAIL_STORAGE_KEY, '0');
    } catch {
      /* ignore */
    }
    setRailOpen(false);
  }, []);

  // Modern space shortcut: Ctrl/⌘+B toggles chat history, Ctrl/⌘+Shift+B
  // (handled in AppShell) toggles global nav. Hide both => full-width focus
  // mode. Never hijack keystrokes while typing.
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
        !event.shiftKey &&
        event.key.toLowerCase() === 'b'
      ) {
        event.preventDefault();
        toggleRail();
      }
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [toggleRail]);

  const focusMode = !navOpen && !railOpen;

  // When the shell is on a narrow viewport the rail is an overlay: opening it
  // must never push the stream column aside.
  return (
    <div
      className={`chat-workspace${focusMode ? ' chat-workspace--focus' : ''}${navOpen ? '' : ' chat-workspace--nav-hidden'}`}
    >
      <ChatHeader
        railOpen={railOpen}
        onToggleRail={toggleRail}
        navOpen={navOpen}
        onToggleNav={toggleNav}
      />
      <div className="chat-workspace__body">
        {isNarrow && railOpen && (
          <button
            type="button"
            className="chat-workspace__rail-backdrop"
            aria-label="Close chat history"
            onClick={closeRail}
          />
        )}
        <HistoryRail
          open={railOpen}
          onNewChat={() => {
            if (isNarrow) closeRail();
          }}
        />
        <div className="chat-workspace__main">
          {focusMode && !isNarrow && (
            <p className="chat-workspace__focus-hint" role="status">
              Focus mode — both sidebars hidden for max space. Press
              <kbd>Ctrl B</kbd> for history, <kbd>Ctrl Shift B</kbd> for menu.
            </p>
          )}
          <MessageStream />
          <Composer />
        </div>
      </div>
    </div>
  );
}