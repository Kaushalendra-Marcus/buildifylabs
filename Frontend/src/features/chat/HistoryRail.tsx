/**
 * HistoryRail (F2) — the collapsible chat-history rail (specs/14 §3): 0 /
 * 280px wide, collapsed by default below 768px and rendered as an OVERLAY on
 * narrow viewports (it never pushes the stream column).
 *
 * Grouped like the design mockups: a New chat action on top, then threads
 * bucketed into TODAY / YESTERDAY / older month-day groups (e.g. JUL 31),
 * newest first, with the active thread highlighted. Amber accent only — no
 * purple anywhere (workspace intelligence theme).
 */
import { Plus } from 'lucide-react';
import { AccountMenu } from './AccountMenu';
import { useChatStore } from './chat-store';
import { useNow } from '../../hooks/useNow';

interface HistoryRailProps {
  open: boolean;
  onNewChat?: () => void;
}

function startOfDay(timestamp: number): number {
  const date = new Date(timestamp);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

function groupLabel(updatedAt: number, now: number): string {
  const day = 24 * 60 * 60 * 1000;
  const todayStart = startOfDay(now);
  if (updatedAt >= todayStart) return 'Today';
  if (updatedAt >= todayStart - day) return 'Yesterday';
  return new Date(updatedAt)
    .toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    .toUpperCase();
}

function groupKey(updatedAt: number, now: number): string {
  const day = 24 * 60 * 60 * 1000;
  const todayStart = startOfDay(now);
  if (updatedAt >= todayStart) return 'today';
  if (updatedAt >= todayStart - day) return 'yesterday';
  const date = new Date(updatedAt);
  return `day-${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

export function HistoryRail({ open, onNewChat }: HistoryRailProps) {
  const conversations = useChatStore((state) => state.conversations);
  const activeId = useChatStore((state) => state.activeConversationId);
  const selectConversation = useChatStore((state) => state.selectConversation);
  const newChat = useChatStore((state) => state.newChat);

  function handleNewChat() {
    newChat();
    onNewChat?.();
  }

  // Day buckets need wall-clock "now" — read it via `useNow` (the sanctioned
  // ticking hook), never `Date.now()` during render. Before the first tick,
  // fall back to the newest thread so groups stay deterministic in tests.
  const tickingNow = useNow();
  const latestUpdatedAt = conversations.reduce(
    (max, conversation) => Math.max(max, conversation.updatedAt),
    0,
  );
  const now = tickingNow ?? latestUpdatedAt;
  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
  const groups: Array<{ label: string; items: typeof conversations }> = [];
  const groupIndex = new Map<string, number>();
  for (const conversation of sorted) {
    const key = groupKey(conversation.updatedAt, now);
    const existing = groupIndex.get(key);
    if (existing === undefined) {
      groupIndex.set(key, groups.length);
      groups.push({
        label: groupLabel(conversation.updatedAt, now),
        items: [conversation],
      });
    } else {
      groups[existing].items.push(conversation);
    }
  }

  return (
    <aside
      className={`history-rail${open ? ' history-rail--open' : ' history-rail--closed'}`}
      aria-label="Chat history"
    >
      <div className="history-rail__top">
        <button
          type="button"
          className="history-rail__new-chat"
          onClick={handleNewChat}
        >
          <Plus size={15} aria-hidden="true" />
          New chat
        </button>
      </div>
      <div className="history-rail__body">
        {sorted.length === 0 ? (
          <>
            <h2 className="history-rail__heading">Conversations</h2>
            <p className="history-rail__placeholder">
              Your conversations will appear here.
            </p>
          </>
        ) : (
          groups.map((group) => (
            <section
              key={group.label}
              className="history-rail__group"
              aria-label={group.label}
            >
              <h2 className="history-rail__heading">{group.label}</h2>
              <ul className="history-rail__list">
                {group.items.map((conversation) => {
                  const isActive = conversation.id === activeId;
                  return (
                    <li key={conversation.id} className="history-rail__item">
                      <button
                        type="button"
                        className={`history-rail__conversation${isActive ? ' history-rail__conversation--active' : ''}`}
                        aria-current={isActive ? 'true' : undefined}
                        title={conversation.title}
                        onClick={() => selectConversation(conversation.id)}
                      >
                        {conversation.title}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))
        )}
      </div>
      <div className="history-rail__footer">
        <AccountMenu align="up" />
      </div>
    </aside>
  );
}
