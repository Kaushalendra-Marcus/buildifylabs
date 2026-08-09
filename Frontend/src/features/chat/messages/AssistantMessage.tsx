/**
 * AssistantMessage dispatcher — maps a `PipelineOutput` to one of the three
 * assistant message kinds (specs/14 §4.2–4.4) via `classifyAssistantOutput`:
 *   - answer (4.2) → AssistantAnswer (prose, visuals grid, insights, trust
 *     footer, news row)
 *   - clarification (4.3) → ClarificationMessage (quick-pick, accent edge)
 *   - fallback (4.4) → FallbackMessage (degraded neutral notice) — unless the
 *     user has no data at all, in which case the fallback is the no-data
 *     messaging (F6 §6 / 07 edge case 2), not a generic empty.
 */
import type { AssistantChatMessage } from '../chat-store';
import { classifyAssistantOutput } from '../chat-store';
import { useChatStore } from '../chat-store';
import { AssistantAnswer } from './AssistantAnswer';
import { ClarificationMessage } from './ClarificationMessage';
import { FallbackMessage } from './FallbackMessage';
import { NoDataMessage } from './NoDataMessage';

export function AssistantMessage({ message }: { message: AssistantChatMessage }) {
  const hasData = useChatStore((state) => state.hasData);
  const kind = classifyAssistantOutput(message.output);
  if (kind === 'clarification') {
    return <ClarificationMessage output={message.output} />;
  }
  if (kind === 'fallback') {
    // A degraded response while the user provably has no data is the no-data
    // messaging (07 edge case 2), not a generic "couldn't answer" notice.
    if (hasData === false) {
      return <NoDataMessage />;
    }
    return <FallbackMessage />;
  }
  return <AssistantAnswer output={message.output} />;
}