/**
 * SystemNotice dispatcher (F5, specs/14 §5.6) — renders the store's `system`
 * messages. The two quota-429 states are deliberately distinct:
 *   - window-exhausted → a transient inline notice with the reset time; the
 *     input stays enabled for the next window.
 *   - lifetime-cap    → a permanent-feeling CARD (not a toast) carrying the
 *     inline contact form (specs/02 FR5).
 *   - error           → transient generic send-failure text.
 */
import type { SystemChatMessage } from '../chat-store';
import { WindowExhaustedNotice } from './WindowExhaustedNotice';
import { LifetimeCapNotice } from './LifetimeCapNotice';

export function SystemNotice({ message }: { message: SystemChatMessage }) {
  if (message.kind === 'window-exhausted') {
    return <WindowExhaustedNotice resetAt={message.resetAt ?? null} />;
  }
  if (message.kind === 'lifetime-cap') {
    return <LifetimeCapNotice />;
  }
  return (
    <div className="quota-notice quota-notice--error" role="alert">
      {message.text ?? 'Something went wrong. Please try again.'}
    </div>
  );
}