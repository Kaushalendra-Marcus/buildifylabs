/**
 * ActivityPage — usage + history (`/app/activity`). Read-only over existing
 * client state: quota-store (window/lifetime counters), chat-store threads
 * (per-conversation message counts) and system notices (the two 429 states +
 * send errors). The backend stays the authority — this page displays, never
 * enforces.
 */
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useNow } from '../../hooks/useNow';
import { useChatStore } from '../chat/chat-store';
import {
  LIFETIME_QUESTIONS_LIMIT,
  WINDOW_HOURS,
  WINDOW_MS,
  WINDOW_QUESTIONS_LIMIT,
  useQuotaStore,
} from '../chat/quota-store';
import './activity.css';

function formatCountdown(ms: number): string {
  if (ms <= 0) return 'any moment';
  const totalMinutes = Math.ceil(ms / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours <= 0) return `in ${minutes}m`;
  return `in ${hours}h ${minutes}m`;
}

const NOTICE_LABEL: Record<string, string> = {
  'window-exhausted': 'Window exhausted',
  'lifetime-cap': 'Lifetime cap reached',
  error: 'Send error',
};

export function ActivityPage() {
  const navigate = useNavigate();
  const now = useNow(30000);
  const messages = useChatStore((s) => s.messages);
  const conversations = useChatStore((s) => s.conversations);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const questionsInWindow = useQuotaStore((s) => s.questionsInWindow);
  const questionsLifetime = useQuotaStore((s) => s.questionsLifetime);
  const windowStartedAt = useQuotaStore((s) => s.windowStartedAt);

  const windowLeft = Math.max(0, WINDOW_QUESTIONS_LIMIT - questionsInWindow);
  const lifetimeLeft = Math.max(0, LIFETIME_QUESTIONS_LIMIT - questionsLifetime);
  const resetIn =
    windowStartedAt === null || now === null
      ? null
      : windowStartedAt + WINDOW_MS - now;

  const threads = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
  const notices = messages.filter((m) => m.role === 'system').slice(-10).reverse();

  function openConversation(conversationId: string) {
    selectConversation(conversationId);
    navigate('/app/chat');
  }

  return (
    <div className="activity-page" role="main" aria-label="Activity and usage">
      <section className="activity-page__head">
        <div>
          <p className="activity-page__eyebrow">Activity</p>
          <h1 className="activity-page__title">Usage & history</h1>
          <p className="activity-page__sub">
            Free plan: {WINDOW_QUESTIONS_LIMIT} questions per {WINDOW_HOURS}h
            window, {LIFETIME_QUESTIONS_LIMIT} lifetime.
          </p>
        </div>
      </section>

      <section className="activity-page__kpis" aria-label="Quota usage">
        <article className="activity-page__card">
          <p className="activity-page__card-label">Window quota</p>
          <p className="activity-page__card-value">
            {windowLeft}
            <span className="activity-page__card-unit">
              {' '}
              of {WINDOW_QUESTIONS_LIMIT} left
            </span>
          </p>
          <div
            className="activity-page__meter"
            role="meter"
            aria-label="Window quota left"
            aria-valuemin={0}
            aria-valuemax={WINDOW_QUESTIONS_LIMIT}
            aria-valuenow={windowLeft}
          >
            <i style={{ width: `${(windowLeft / WINDOW_QUESTIONS_LIMIT) * 100}%` }} />
          </div>
          <p className="activity-page__card-note">
            {resetIn === null
              ? 'No questions asked yet this session.'
              : `Resets ${formatCountdown(resetIn)}`}
          </p>
        </article>
        <article className="activity-page__card">
          <p className="activity-page__card-label">Lifetime quota</p>
          <p className="activity-page__card-value">
            {lifetimeLeft}
            <span className="activity-page__card-unit">
              {' '}
              of {LIFETIME_QUESTIONS_LIMIT} left
            </span>
          </p>
          <div
            className="activity-page__meter"
            role="meter"
            aria-label="Lifetime quota left"
            aria-valuemin={0}
            aria-valuemax={LIFETIME_QUESTIONS_LIMIT}
            aria-valuenow={lifetimeLeft}
          >
            <i style={{ width: `${(lifetimeLeft / LIFETIME_QUESTIONS_LIMIT) * 100}%` }} />
          </div>
          <p className="activity-page__card-note">
            {lifetimeLeft === 0
              ? 'Cap reached — use the contact form in the chat notice.'
              : 'Never resets. Ask the important ones.'}
          </p>
        </article>
      </section>

      <div className="activity-page__grid">
        <section className="activity-page__panel" aria-label="Conversations">
          <h2 className="activity-page__panel-title">Conversations</h2>
          {threads.length === 0 ? (
            <p className="activity-page__empty">
              No threads yet. Your chats will appear here with message counts.
            </p>
          ) : (
            <ul className="activity-page__threads">
              {threads.map((thread) => {
                const count = messages.filter(
                  (m) =>
                    (m as { conversationId?: string }).conversationId === thread.id,
                ).length;
                return (
                  <li key={thread.id} className="activity-page__thread">
                    <div>
                      <p className="activity-page__thread-title">{thread.title}</p>
                      <p className="activity-page__thread-meta">
                        {count} messages ·{' '}
                        {new Date(thread.updatedAt).toLocaleString()}
                      </p>
                    </div>
                    <button
                      type="button"
                      className="activity-page__inline-link"
                      onClick={() => openConversation(thread.id)}
                    >
                      Open <ArrowRight size={13} aria-hidden="true" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="activity-page__panel" aria-label="Notices">
          <h2 className="activity-page__panel-title">Notices</h2>
          {notices.length === 0 ? (
            <p className="activity-page__empty">
              No quota or error notices. They land here when a limit hits.
            </p>
          ) : (
            <ul className="activity-page__notices">
              {notices.map((notice) => {
                if (notice.role !== 'system') return null;
                return (
                  <li key={notice.id} className="activity-page__notice">
                    <p className="activity-page__notice-kind">
                      {NOTICE_LABEL[notice.kind] ?? notice.kind}
                    </p>
                    {notice.text && (
                      <p className="activity-page__notice-text">{notice.text}</p>
                    )}
                    <button
                      type="button"
                      className="activity-page__inline-link"
                      onClick={() => openConversation(notice.conversationId)}
                    >
                      View thread <ArrowRight size={13} aria-hidden="true" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
