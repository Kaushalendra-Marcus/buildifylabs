/**
 * FollowUpChips — tap-to-ask follow-up questions under a normal answer.
 *
 * Sends the tapped question as a brand-new user message through the same
 * quota-aware path as a clarification pill (mirrors ClarificationMessage's
 * send flow; kept local so neither component owns the other). Renders nothing
 * when the answer carries no follow-ups.
 */
import { sendQuery } from '../../../api/chat';
import { useChatStore } from '../chat-store';
import { useQuotaStore, WINDOW_MS } from '../quota-store';
import { useScopeStore } from '../scope-store';
import { getErrorMessage } from '../../../lib/errors';
import { isQuotaError } from '../../../lib/http';

export function FollowUpChips({ followups }: { followups: string[] }) {
  const addUserMessage = useChatStore((state) => state.addUserMessage);
  const addAssistantMessage = useChatStore((state) => state.addAssistantMessage);
  const addSystemNotice = useChatStore((state) => state.addSystemNotice);
  const setPending = useChatStore((state) => state.setPending);
  const scope = useScopeStore((state) => state.scope);

  if (followups.length === 0) return null;

  const handleFollowUp = async (question: string) => {
    addUserMessage(question);
    setPending('thinking');

    try {
      const response = await sendQuery({
        query: question,
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
    <div className="followup-chips" aria-label="Suggested follow-up questions">
      {followups.map((question) => (
        <button
          key={question}
          type="button"
          className="followup-chips__chip"
          onClick={() => void handleFollowUp(question)}
        >
          {question}
        </button>
      ))}
    </div>
  );
}
