/**
 * FallbackMessage (specs/14 §4.4) — distinct neutral notice when the pipeline
 * degrades to its safe fallback (confidence = 0.0, no visuals). Never render
 * a normal answer block next to a suspiciously empty confidence meter.
 *
 * The backend's own reason (already neutral copy like "I ran into a problem
 * gathering evidence - please try again.") renders underneath so a failure
 * names its step instead of hiding behind one static line.
 */
import { CircleHelp } from 'lucide-react';

export function FallbackMessage({ reason }: { reason?: string | null }) {
  const detail = (reason ?? '').trim();
  return (
    <div className="message message--fallback">
      <CircleHelp size={16} aria-hidden="true" />
      <span>Couldn't produce a reliable answer for that</span>
      {detail.length > 0 && (
        <span className="message--fallback__reason">{detail}</span>
      )}
    </div>
  );
}