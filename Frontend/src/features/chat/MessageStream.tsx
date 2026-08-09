/**
 * MessageStream (F2 shell) — the message-stream column region (specs/14 §3):
 * the ONLY place visuals render. It scrolls within the workspace column; F3
 * fills it with the four message types, F6 the empty-thread states.
 */
export function MessageStream() {
  return (
    <div className="message-stream" role="region" aria-label="Message stream">
      {/* Messages render here (F3 / F6). */}
    </div>
  );
}