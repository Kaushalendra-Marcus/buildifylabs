/**
 * AssistantMessage dispatcher — maps a `PipelineOutput` to one of the three
 * assistant message kinds (specs/14 §4.2–4.4) via `classifyAssistantOutput`:
 *   - answer (4.2) → AssistantAnswer (prose, visuals grid, insights, trust
 *     footer, news row)
 *   - clarification (4.3) → ClarificationMessage (quick-pick, accent edge)
 *   - fallback (4.4) → FallbackMessage (degraded neutral notice)
 */
import type { AssistantChatMessage } from '../chat-store';
import { classifyAssistantOutput } from '../chat-store';
import { AssistantAnswer } from './AssistantAnswer';
import { ClarificationMessage } from './ClarificationMessage';
import { FallbackMessage } from './FallbackMessage';

export function AssistantMessage({ message }: { message: AssistantChatMessage }) {
  const kind = classifyAssistantOutput(message.output);
  if (kind === 'clarification') {
    return <ClarificationMessage output={message.output} />;
  }
  if (kind === 'fallback') {
    return <FallbackMessage />;
  }
  return <AssistantAnswer output={message.output} />;
}