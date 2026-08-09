/**
 * ColdStartNotice (F5, specs/14 §5.7) — the named first-load state, shown ONLY
 * on a session's first request and distinct from the per-message thinking
 * indicator (F6). Not a bare spinner: a named message with a
 * determinate-feeling progress element.
 */
import { Thermometer } from 'lucide-react';

export function ColdStartNotice() {
  return (
    <div className="cold-start-notice" role="status">
      <div className="cold-start-notice__row">
        <Thermometer size={16} aria-hidden="true" />
        <span>Waking up the server — first load can take up to a minute</span>
      </div>
      <div
        className="cold-start-notice__progress"
        role="progressbar"
        aria-label="Waking up the server"
      >
        <div className="cold-start-notice__progress-bar" />
      </div>
    </div>
  );
}