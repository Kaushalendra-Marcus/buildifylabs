/**
 * AssistantIdentity — the "Buildify Intelligence" eyebrow row above assistant
 * answers and clarifications (logo tile + label), matching the workspace's
 * intelligence styling. Purely presentational; carries no message state.
 */
export function AssistantIdentity() {
  return (
    <p className="assistant-identity">
      <span className="brand-tile brand-tile--sm" aria-hidden="true">
        <img src="/logo.png" alt="" />
      </span>
      Buildify Intelligence
    </p>
  );
}
