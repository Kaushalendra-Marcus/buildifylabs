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
import { useState } from 'react';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { ChatHeader } from './ChatHeader';
import { HistoryRail } from './HistoryRail';
import { MessageStream } from './MessageStream';
import { Composer } from './Composer';
import './chat-workspace.css';

const NARROW_MAX_QUERY = '(max-width: 767.98px)';

export function ChatWorkspace() {
  const isNarrow = useMediaQuery(NARROW_MAX_QUERY);
  // Rail defaults: open on desktop (>=768px), collapsed by default <768px
  // (specs/14 §3). Only the initial value follows the viewport — the toggle is
  // the user's after that.
  const [railOpen, setRailOpen] = useState(() =>
    typeof window === 'undefined' || typeof window.matchMedia !== 'function'
      ? true
      : !window.matchMedia(NARROW_MAX_QUERY).matches,
  );

  // When the shell is on a narrow viewport the rail is an overlay: opening it
  // must never push the stream column aside.
  return (
    <div className="chat-workspace">
      <ChatHeader
        railOpen={railOpen}
        onToggleRail={() => setRailOpen((open) => !open)}
      />
      <div className="chat-workspace__body">
        {isNarrow && railOpen && (
          <button
            type="button"
            className="chat-workspace__rail-backdrop"
            aria-label="Close chat history"
            onClick={() => setRailOpen(false)}
          />
        )}
        <HistoryRail open={railOpen} />
        <div className="chat-workspace__main">
          <MessageStream />
          <Composer />
        </div>
      </div>
    </div>
  );
}