/**
 * ClarificationMessage (specs/14 §4.3) — visually distinct from the answer:
 * accent left edge, the `question`, and `options[]` as tappable pill buttons.
 * Tapping one APPENDS it to the original user query and sends as a follow-up,
 * preserving context. A free-text reply box offers the same path for answers
 * that fit no preset option. No answer body, no cards, no trust footer.
 */
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import { SendHorizontal } from 'lucide-react';
import { sendQuery } from '../../../api/chat';
import { useChatStore } from '../chat-store';
import { useQuotaStore, WINDOW_MS } from '../quota-store';
import { useScopeStore } from '../scope-store';
import type { PipelineOutput } from '../../../types/chat';
import { getErrorMessage } from '../../../lib/errors';
import { isQuotaError } from '../../../lib/http';
import { AssistantIdentity } from './AssistantIdentity';

export function ClarificationMessage({ output }: { output: PipelineOutput }) {
  const messages = useChatStore((state) => state.messages);
  const addUserMessage = useChatStore((state) => state.addUserMessage);
  const addAssistantMessage = useChatStore((state) => state.addAssistantMessage);
  const addSystemNotice = useChatStore((state) => state.addSystemNotice);
  const setPending = useChatStore((state) => state.setPending);
  const scope = useScopeStore((state) => state.scope);
  const [customReply, setCustomReply] = useState('');
  const customInputId = useId();

  const clarification = output.clarification;
  if (!clarification) return null;

  // Find the original user message that prompted this clarification
  const originalUserMessage = [...messages]
    .reverse()
    .find((msg) => msg.role === 'user')?.content || '';

  const sendFollowUp = async (answer: string) => {
    // Combine the original query with the clarification response
    const followUp = `${originalUserMessage} - ${answer}`;
    addUserMessage(followUp);
    setPending('thinking');

    try {
      const response = await sendQuery({
        query: followUp,
        source_scope: scope,
      });
      useQuotaStore.getState().recordQuestion();
      addAssistantMessage(response);
    } catch (caught) {
      if (isQuotaError(caught)) {
        if (caught.body.contact_form) {
          useQuotaStore.getState().applyLifetimeExhausted();
          addSystemNotice('lifetime-cap');
        } else {
          useQuotaStore.getState().applyWindowExhausted();
          const started = useQuotaStore.getState().windowStartedAt;
          addSystemNotice(
            'window-exhausted',
            started === null ? null : started + WINDOW_MS,
          );
        }
      } else {
        addSystemNotice('error', null, getErrorMessage(caught));
      }
    } finally {
      setPending(null);
    }
  };

  const handleCustomSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = customReply.trim();
    if (!text) return;
    setCustomReply('');
    void sendFollowUp(text);
  };

  return (
    <div className="message message--clarification">
      <AssistantIdentity />
      <p className="message__clarification-eyebrow">Clarification needed</p>
      <p className="message__clarification-question">{clarification.question}</p>
      {clarification.options.length > 0 && (
        <div className="message__clarification-options">
          {clarification.options.map((option) => (
            <button
              key={option}
              type="button"
              className="message__clarification-option"
              onClick={() => void sendFollowUp(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}
      <form
        className="message__clarification-custom"
        onSubmit={handleCustomSubmit}
      >
        <label
          className="message__clarification-custom-label"
          htmlFor={customInputId}
        >
          Or type your own answer
        </label>
        <div className="message__clarification-custom-row">
          <input
            id={customInputId}
            className="message__clarification-custom-input"
            type="text"
            value={customReply}
            onChange={(event) => setCustomReply(event.target.value)}
            placeholder="Type your answer…"
            autoComplete="off"
          />
          <button
            type="submit"
            className="message__clarification-custom-send"
            disabled={customReply.trim().length === 0}
            aria-label="Send custom answer"
            title="Send custom answer"
          >
            <SendHorizontal size={16} aria-hidden="true" />
          </button>
        </div>
      </form>
    </div>
  );
}