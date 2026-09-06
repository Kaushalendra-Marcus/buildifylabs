/**
 * ClarificationMessage (specs/14 §4.3) — visually distinct from the answer:
 * accent left edge, the `question`, and `options[]` as tappable pill buttons.
 * Tapping one APPENDS it to the original user query and sends as a follow-up,
 * preserving context. No answer body, no cards, no trust footer.
 */
import { sendQuery } from '../../../api/chat';
import { useChatStore } from '../chat-store';
import { useQuotaStore, WINDOW_MS } from '../quota-store';
import { useScopeStore } from '../scope-store';
import type { PipelineOutput } from '../../../types/chat';
import { getErrorMessage } from '../../../lib/errors';
import { isQuotaError } from '../../../lib/http';

export function ClarificationMessage({ output }: { output: PipelineOutput }) {
  const messages = useChatStore((state) => state.messages);
  const addUserMessage = useChatStore((state) => state.addUserMessage);
  const addAssistantMessage = useChatStore((state) => state.addAssistantMessage);
  const addSystemNotice = useChatStore((state) => state.addSystemNotice);
  const setPending = useChatStore((state) => state.setPending);
  const scope = useScopeStore((state) => state.scope);

  const clarification = output.clarification;
  if (!clarification) return null;

  // Find the original user message that prompted this clarification
  const originalUserMessage = [...messages]
    .reverse()
    .find((msg) => msg.role === 'user')?.content || '';

  const handleOptionClick = async (option: string) => {
    // Combine the original query with the clarification response
    const followUp = `${originalUserMessage} - ${option}`;
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

  return (
    <div className="message message--clarification">
      <p className="message__clarification-question">{clarification.question}</p>
      {clarification.options.length > 0 && (
        <div className="message__clarification-options">
          {clarification.options.map((option) => (
            <button
              key={option}
              type="button"
              className="message__clarification-option"
              onClick={() => void handleOptionClick(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}