/**
 * ThinkingIndicator (F6, specs/14 §6) — the small inline "assistant is
 * thinking" indicator shown under the in-flight user message. Deliberately
 * distinct from the F5 cold-start state (§5.7): a tiny right-aligned row of
 * dots, not a full-width card.
 */
export function ThinkingIndicator({
  liveWeb = false,
  stage = 'thinking',
}: {
  liveWeb?: boolean;
  stage?: 'searching' | 'judging' | 'thinking';
}) {
  const label = !liveWeb
    ? 'Thinking'
    : stage === 'searching'
      ? 'Searching the internet'
      : stage === 'judging'
        ? 'Checking whether the evidence is sufficient'
        : 'Using the verified results to prepare your answer';
  return (
    <div
      className="thinking-indicator"
      role="status"
      aria-label={liveWeb ? label : 'Assistant is thinking'}
    >
      <span className="thinking-indicator__label">
        {label}
      </span>
      <span className="thinking-indicator__dot" />
      <span className="thinking-indicator__dot" />
      <span className="thinking-indicator__dot" />
    </div>
  );
}
