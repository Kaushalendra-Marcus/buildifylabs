/**
 * OverviewPage — the `/app` landing dashboard. Read-only over existing
 * state: chat-store (questions + recent answers), quota-store (quota left),
 * GET /files (datasets). No new backend. Quick-ask templates ride to the
 * chat via sessionStorage (`bl-pending-question`) which the Composer picks
 * up on mount — one click, no duplicated send logic.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Database, MessageSquare, Pin, Sparkles } from 'lucide-react';
import { listFiles } from '../../api/files';
import { getErrorMessage } from '../../lib/errors';
import { useAuth } from '../../hooks/useAuth';
import type { FileResponse } from '../../types';
import { useChatStore } from '../chat/chat-store';
import {
  LIFETIME_QUESTIONS_LIMIT,
  WINDOW_QUESTIONS_LIMIT,
  useQuotaStore,
} from '../chat/quota-store';
import { useReportsStore } from '../reports/reports-store';
import './overview.css';

const QUICK_ASKS = [
  'Why did revenue drop last week?',
  'Show month-over-month growth',
  'Top 5 products by revenue',
  'Which region is underperforming?',
  'Forecast next quarter',
  'Compare sales by channel',
];

const PENDING_KEY = 'bl-pending-question';
const ONBOARDED_KEY = 'bl-onboarded-v1';

export function OverviewPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const messages = useChatStore((s) => s.messages);
  const conversations = useChatStore((s) => s.conversations);
  const hasData = useChatStore((s) => s.hasData);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const questionsInWindow = useQuotaStore((s) => s.questionsInWindow);
  const questionsLifetime = useQuotaStore((s) => s.questionsLifetime);
  const pinnedCount = useReportsStore((s) => s.reports.length);

  const [files, setFiles] = useState<FileResponse[]>([]);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [filesLoading, setFilesLoading] = useState(true);
  const [onboarded, setOnboarded] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(ONBOARDED_KEY) !== null ||
        localStorage.getItem(ONBOARDED_KEY) !== null;
    } catch {
      return true; // storage unavailable — never nag
    }
  });

  function dismissOnboarding() {
    try {
      localStorage.setItem(ONBOARDED_KEY, '1');
    } catch {
      /* storage unavailable — banner just hides for this render */
    }
    setOnboarded(true);
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const fetched = await listFiles();
        if (!cancelled) {
          setFiles(fetched);
          setFilesError(null);
        }
      } catch (caught) {
        if (!cancelled) setFilesError(getErrorMessage(caught));
      } finally {
        if (!cancelled) setFilesLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = useMemo(() => {
    const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const assistantAnswers = messages.filter((m) => m.role === 'assistant');
    const recentAnswers = assistantAnswers.filter(
      (m) => (m.createdAt ?? 0) >= weekAgo,
    );
    const lastAssistant = [...assistantAnswers].reverse()[0];
    const lastOutput =
      lastAssistant && lastAssistant.role === 'assistant' ? lastAssistant.output : null;
    return {
      questionsWeek: recentAnswers.length,
      threads: conversations.length,
      lastAnswer: lastOutput?.answer ?? null,
      lastConfidence: lastOutput?.confidence ?? null,
      lastVisuals: lastOutput?.visuals.length ?? 0,
    };
  }, [messages, conversations]);

  const recentAssistant = useMemo(
    () =>
      messages
        .filter((m) => m.role === 'assistant')
        .slice(-3)
        .reverse(),
    [messages],
  );

  const windowLeft = Math.max(0, WINDOW_QUESTIONS_LIMIT - questionsInWindow);
  const lifetimeLeft = Math.max(0, LIFETIME_QUESTIONS_LIMIT - questionsLifetime);
  const displayName = user?.name?.trim() || user?.email?.trim() || 'there';
  const hasDatasets = files.length > 0 || hasData === true;

  function quickAsk(question: string) {
    try {
      sessionStorage.setItem(PENDING_KEY, question);
    } catch {
      /* storage unavailable — chat still opens */
    }
    navigate('/app/chat');
  }

  function openConversation(conversationId: string) {
    selectConversation(conversationId);
    navigate('/app/chat');
  }

  return (
    <div className="overview" role="main" aria-label="Overview dashboard">
      {!onboarded && (
        <section className="overview__welcome" aria-label="Welcome">
          <div>
            <p className="overview__welcome-title">Welcome to BuildifyLabs 👋</p>
            <p className="overview__welcome-sub">
              Three steps and you are answering questions from your own data.
              Press <kbd>Ctrl+K</kbd> anywhere to jump around.
            </p>
          </div>
          <button
            type="button"
            className="overview__welcome-dismiss"
            onClick={dismissOnboarding}
          >
            Got it
          </button>
        </section>
      )}
      <section className="overview__hero">
        <div>
          <p className="overview__eyebrow">Overview</p>
          <h1 className="overview__title">Namaste, {displayName}.</h1>
          <p className="overview__sub">
            {hasDatasets
              ? 'Your data is in. Ask a follow-up or pin your best chart to Reports.'
              : 'Upload your first CSV or spreadsheet, then ask your first question.'}
          </p>
        </div>
        <div className="overview__hero-actions">
          {!hasDatasets ? (
            <Link to="/app/data" className="overview__primary">
              <Database size={16} aria-hidden="true" />
              Upload data
            </Link>
          ) : (
            <Link to="/app/chat" className="overview__primary">
              <MessageSquare size={16} aria-hidden="true" />
              Ask a question
            </Link>
          )}
          <Link to="/app/reports" className="overview__ghost">
            <Pin size={16} aria-hidden="true" />
            View reports
          </Link>
        </div>
      </section>

      <section className="overview__kpis" aria-label="Key numbers">
        <article className="overview__card">
          <p className="overview__card-label">Questions · 7 days</p>
          <p className="overview__card-value">{stats.questionsWeek}</p>
          <p className="overview__card-note">{stats.threads} threads total</p>
        </article>
        <article className="overview__card">
          <p className="overview__card-label">Datasets</p>
          <p className="overview__card-value">
            {filesLoading ? '…' : files.length}
          </p>
          <p className="overview__card-note">
            {filesError ?? (hasDatasets ? 'Ready to query' : 'No data yet')}
          </p>
        </article>
        <article className="overview__card">
          <p className="overview__card-label">Quota left</p>
          <p className="overview__card-value">
            {windowLeft}
            <span className="overview__card-unit"> / window</span>
          </p>
          <p className="overview__card-note">{lifetimeLeft} lifetime left</p>
        </article>
        <article className="overview__card">
          <p className="overview__card-label">Pinned reports</p>
          <p className="overview__card-value">{pinnedCount}</p>
          <p className="overview__card-note">
            {stats.lastVisuals > 0
              ? `${stats.lastVisuals} visuals in last answer`
              : 'Pin charts from chat'}
          </p>
        </article>
      </section>

      <div className="overview__grid">
        <section className="overview__panel" aria-label="Getting started">
          <h2 className="overview__panel-title">Getting started</h2>
          <ol className="overview__checklist">
            <li data-done={hasDatasets ? 'true' : 'false'}>
              <span aria-hidden="true">{hasDatasets ? '✓' : '1'}</span>
              Upload a CSV, PDF, or XLSX
              {!hasDatasets && (
                <Link to="/app/data" className="overview__inline-link">
                  Go to Data <ArrowRight size={13} aria-hidden="true" />
                </Link>
              )}
            </li>
            <li data-done={messages.length > 0 ? 'true' : 'false'}>
              <span aria-hidden="true">{messages.length > 0 ? '✓' : '2'}</span>
              Ask your first question
              {messages.length === 0 && (
                <Link to="/app/chat" className="overview__inline-link">
                  Go to Chat <ArrowRight size={13} aria-hidden="true" />
                </Link>
              )}
            </li>
            <li data-done={pinnedCount > 0 ? 'true' : 'false'}>
              <span aria-hidden="true">{pinnedCount > 0 ? '✓' : '3'}</span>
              Pin a chart to Reports
              {pinnedCount === 0 && messages.length > 0 && (
                <span className="overview__hint">
                  Use “Pin to reports” under any answer.
                </span>
              )}
            </li>
          </ol>

          <h3 className="overview__panel-subtitle">
            <Sparkles size={14} aria-hidden="true" />
            Try one question
          </h3>
          <ul className="overview__quick-asks" aria-label="Starter questions">
            {QUICK_ASKS.map((q) => (
              <li key={q}>
                <button
                  type="button"
                  className="overview__chip"
                  onClick={() => quickAsk(q)}
                >
                  {q}
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="overview__panel" aria-label="Recent insights">
          <h2 className="overview__panel-title">Recent insights</h2>
          {recentAssistant.length === 0 ? (
            <p className="overview__empty">
              No answers yet. Your last 3 traced answers will appear here.
            </p>
          ) : (
            <ul className="overview__recent">
              {recentAssistant.map((m) => {
                if (m.role !== 'assistant') return null;
                return (
                  <li key={m.id} className="overview__recent-item">
                    <p className="overview__recent-answer">
                      {m.output.answer.slice(0, 160)}
                      {m.output.answer.length > 160 ? '…' : ''}
                    </p>
                    <p className="overview__recent-meta">
                      {m.output.visuals.length} visuals
                      {Number.isFinite(m.output.confidence)
                        ? ` · Confidence ${Math.round(m.output.confidence * 100)}%`
                        : ''}
                    </p>
                    <button
                      type="button"
                      className="overview__inline-link"
                      onClick={() => openConversation(m.conversationId)}
                    >
                      Open in chat <ArrowRight size={13} aria-hidden="true" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {stats.lastAnswer && (
            <p className="overview__last">
              Last answer: “{stats.lastAnswer.slice(0, 120)}
              {stats.lastAnswer.length > 120 ? '…' : ''}”
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
