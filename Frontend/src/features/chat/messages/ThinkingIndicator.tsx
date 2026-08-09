/**
 * ThinkingIndicator (F6, specs/14 §6) — the small inline "assistant is
 * thinking" indicator shown under the in-flight user message. Deliberately
 * distinct from the F5 cold-start state (§5.7): a tiny right-aligned row of
 * dots, not a full-width card.
 */
export function ThinkingIndicator() {
  return (
    <div
      className="thinking-indicator"
      role="status"
      aria-label="Assistant is thinking"
    >
      <span className="thinking-indicator__dot" />
      <span className="thinking-indicator__dot" />
      <span className="thinking-indicator__dot" />
    </div>
  );
}
