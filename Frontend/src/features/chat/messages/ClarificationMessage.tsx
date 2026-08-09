/**
 * ClarificationMessage (specs/14 §4.3) — visually distinct from the answer:
 * accent left edge, the `question`, and `options[]` as tappable pill buttons.
 * Tapping one SENDS it verbatim as the next user message. No answer body, no
 * cards, no trust footer (nothing to verify yet).
 */
import { useChatStore } from '../chat-store';
import type { PipelineOutput } from '../../../types/chat';

export function ClarificationMessage({ output }: { output: PipelineOutput }) {
  const addUserMessage = useChatStore((state) => state.addUserMessage);

  const clarification = output.clarification;
  if (!clarification) return null;

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
              onClick={() => addUserMessage(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}