/**
 * MessageStream (F3) — the message-stream column region (specs/14 §3): the
 * ONLY place visuals render. Renders the message list from the chat store —
 * the four message types per specs/14 §4 (user / answer / clarification /
 * fallback) via UserMessage + AssistantMessage.
 */
import { useChatStore } from './chat-store';
import { UserMessage } from './messages/UserMessage';
import { AssistantMessage } from './messages/AssistantMessage';
import './message-stream.css';

export function MessageStream() {
  const messages = useChatStore((state) => state.messages);

  return (
    <div className="message-stream" role="region" aria-label="Message stream">
      <div className="message-stream__list">
        {messages.map((message) =>
          message.role === 'user' ? (
            <UserMessage key={message.id} message={message} />
          ) : (
            <AssistantMessage key={message.id} message={message} />
          ),
        )}
      </div>
    </div>
  );
}