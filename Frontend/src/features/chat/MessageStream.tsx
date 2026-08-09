/**
 * MessageStream (F3 + F5 + F6) — the message-stream column region (specs/14
 * §3): the ONLY place visuals render. Renders the message list from the chat
 * store — the four `specs/14` §4 message types (user / answer / clarification /
 * fallback) plus the F5 system notices (§5.6: window-exhausted inline notice,
 * lifetime-cap card, error), the cold-start first-load state (§5.7), and the
 * F6 remaining states (§6): the empty-thread invite (guest / registered+no
 * files), the no-data question messaging, and the small inline thinking
 * indicator.
 */
import { useChatStore } from './chat-store';
import { UserMessage } from './messages/UserMessage';
import { AssistantMessage } from './messages/AssistantMessage';
import { SystemNotice } from './messages/SystemNotice';
import { ColdStartNotice } from './messages/ColdStartNotice';
import { ThinkingIndicator } from './messages/ThinkingIndicator';
import { EmptyThread } from './messages/EmptyThread';
import './message-stream.css';

export function MessageStream() {
  const messages = useChatStore((state) => state.messages);
  const pending = useChatStore((state) => state.pending);

  // F6 §6: no messages yet → the empty-thread invite (guest / no-files).
  if (messages.length === 0) {
    return (
      <div className="message-stream" role="region" aria-label="Message stream">
        <EmptyThread />
      </div>
    );
  }

  return (
    <div className="message-stream" role="region" aria-label="Message stream">
      <div className="message-stream__list">
        {messages.map((message) => {
          if (message.role === 'user') {
            return <UserMessage key={message.id} message={message} />;
          }
          if (message.role === 'assistant') {
            return <AssistantMessage key={message.id} message={message} />;
          }
          return <SystemNotice key={message.id} message={message} />;
        })}
        {pending === 'cold-start' && <ColdStartNotice />}
        {pending === 'thinking' && <ThinkingIndicator />}
      </div>
    </div>
  );
}
