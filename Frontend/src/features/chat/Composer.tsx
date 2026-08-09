/**
 * Composer (F2 shell) — the composer region pinned to the foot of the message
 * stream column (specs/14 §3): "fixed to bottom of the stream column". F5
 * fills the actual input, source-scope selector, upload and send controls.
 */
export function Composer() {
  return (
    <div className="composer" role="region" aria-label="Composer">
      {/* Composer controls land in F5. */}
    </div>
  );
}