/**
 * HistoryRail (F2) — the collapsible chat-history rail (specs/14 §3): 0 /
 * 280px wide, collapsed by default below 768px and rendered as an OVERLAY on
 * narrow viewports (it never pushes the stream column). Contents numbered by
 * a later phase; the shell owns the open/closed + narrow-overlay behavior.
 */
import { useChatStore } from './chat-store';

interface HistoryRailProps {
  open: boolean;
}

export function HistoryRail({ open }: HistoryRailProps) {
  const conversations = useChatStore((state) => state.conversations);

  return (
    <aside
      className={`history-rail${open ? ' history-rail--open' : ' history-rail--closed'}`}
      aria-label="Chat history"
    >
      <div className="history-rail__body">
        <h2 className="history-rail__heading">Conversations</h2>
        {conversations.length === 0 ? (
          <p className="history-rail__placeholder">Your conversations will appear here.</p>
        ) : (
          <ul className="history-rail__list">
            {conversations.map((conversation) => (
              <li key={conversation.id} className="history-rail__item">
                <button type="button" className="history-rail__conversation">
                  {conversation.title}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}