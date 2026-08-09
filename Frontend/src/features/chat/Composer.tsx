/**
 * Composer (F5, specs/14 §5) — the composer region pinned to the foot of the
 * message stream column. Delivers all five §5 controls:
 *   5.1  multiline auto-grow text input; placeholder is a real example query
 *   5.2  source-scope selector (Your data / Live web / Both), always visible,
 *        defaults to and persists "Your data"; Live web/Both are gated until
 *        B7 — never switched silently (07 FR4), the composer just annotates
 *        and lets the backend's honest fallback answer
 *   5.3  upload button ABSENT for `guest` plans (never shown disabled), opens
 *        the UploadPopover (drag-drop, "CSV, PDF, or XLSX", size hint)
 *   5.4  Send disabled ONLY when the input is empty — never by quota: the
 *        send happens and the 429 surfaces as a proper notice (§5.6)
 *   5.6  the two quota-429 states are store notices rendered in the stream
 *   5.7  cold start: the session's first request marks pending as
 *        `cold-start` (named wake-up state, distinct from thinking)
 */
import { SendHorizontal, Paperclip } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { sendQuery } from '../../api/chat';
import { useAuth } from '../../hooks/useAuth';
import { getErrorMessage } from '../../lib/errors';
import { isQuotaError } from '../../lib/http';
import type { SourceScope } from '../../types/chat';
import { useChatStore, type PendingKind } from './chat-store';
import { useScopeStore } from './scope-store';
import { WINDOW_MS, useQuotaStore } from './quota-store';
import { UploadPopover } from './UploadPopover';
import './composer.css';

/** First request of a session only (§5.7) — plain module flag, resets on reload. */
let coldStartSeen = false;

const SCOPE_SEGMENTS: Array<{ value: SourceScope; label: string }> = [
  { value: 'own_data', label: 'Your data' },
  { value: 'live_web', label: 'Live web' },
  { value: 'both', label: 'Both' },
];

export function Composer() {
  const { user } = useAuth();
  const isGuest = user?.plan === 'guest';

  const scope = useScopeStore((state) => state.scope);
  const setScope = useScopeStore((state) => state.setScope);

  const [draft, setDraft] = useState('');
  const [popoverOpen, setPopoverOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const canSend = draft.trim().length > 0; // §5.4: quota NEVER disables send.

  // 5.1 auto-grow: shrink-free sizing from content (capped, scrolls past max).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  async function submit(text: string) {
    // Guard: identical for the button/submit and the Enter key, and only for
    // non-empty text. A 429 flows through as a quota notice in the stream —
    // the input never gets locked by quota (§5.4/§5.6).
    const pending: PendingKind = coldStartSeen ? 'thinking' : 'cold-start';
    coldStartSeen = true;
    useChatStore.getState().setPending(pending);
    useChatStore
      .getState()
      .addUserMessage(text, useChatStore.getState().activeFileName);

    try {
      const output = await sendQuery({ query: text, source_scope: scope });
      useQuotaStore.getState().recordQuestion();
      useChatStore.getState().addAssistantMessage(output);
    } catch (caught) {
      if (isQuotaError(caught)) {
        if (caught.body.contact_form) {
          useQuotaStore.getState().applyLifetimeExhausted();
          useChatStore.getState().addSystemNotice('lifetime-cap');
        } else {
          useQuotaStore.getState().applyWindowExhausted();
          const started = useQuotaStore.getState().windowStartedAt;
          useChatStore.getState().addSystemNotice(
            'window-exhausted',
            started === null ? null : started + WINDOW_MS,
          );
        }
      } else {
        useChatStore
          .getState()
          .addSystemNotice('error', null, getErrorMessage(caught));
      }
    } finally {
      useChatStore.getState().setPending(null);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft('');
    void submit(text);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault(); // newline stays Shift+Enter
      const text = draft.trim();
      if (!text) return;
      setDraft('');
      void submit(text);
    }
  }

  return (
    <div className="composer" role="region" aria-label="Composer">
      {scope !== 'own_data' && (
        <p className="composer__gated-hint" role="note">
          Live web and Both aren't available yet — answers will fall back to
          your own data.
        </p>
      )}

      <form className="composer__form" onSubmit={handleSubmit}>
        {/* 5.2 source-scope selector — always visible, persisted */}
        <div className="composer__scope" role="group" aria-label="Source scope">
          {SCOPE_SEGMENTS.map((segment) => (
            <button
              key={segment.value}
              type="button"
              className="composer__scope-segment"
              aria-pressed={scope === segment.value}
              title={
                segment.value === 'own_data'
                  ? 'Questions are answered from your uploaded data.'
                  : 'Not available yet — answers fall back to your own data.'
              }
              onClick={() => setScope(segment.value)}
            >
              {segment.label}
            </button>
          ))}
        </div>

        {/* 5.1 text input + 5.3 upload + 5.4 send */}
        <div className="composer__row">
          <textarea
            ref={inputRef}
            className="composer__input"
            value={draft}
            rows={1}
            placeholder="Why did revenue drop last week?"
            aria-label="Ask about your data"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
          />

          {!isGuest && (
            <button
              type="button"
              className="composer__icon-button"
              aria-expanded={popoverOpen}
              aria-haspopup="dialog"
              aria-label="Upload files"
              title="Upload a CSV, PDF, or XLSX"
              onClick={() => setPopoverOpen((value) => !value)}
            >
              <Paperclip size={18} aria-hidden="true" />
            </button>
          )}

          <button
            type="submit"
            className="composer__send"
            disabled={!canSend}
            aria-label="Send message"
            title={canSend ? 'Send question' : 'Type a question first'}
          >
            <SendHorizontal size={18} aria-hidden="true" />
          </button>
        </div>
      </form>

      {popoverOpen && (
        <div className="composer__popover-layer">
          <button
            type="button"
            className="composer__popover-backdrop"
            aria-label="Close upload"
            tabIndex={-1}
            onClick={() => setPopoverOpen(false)}
          />
          <UploadPopover onClose={() => setPopoverOpen(false)} />
        </div>
      )}
    </div>
  );
}