/**
 * FallbackMessage (specs/14 §4.4) — distinct neutral notice when the pipeline
 * degrades to its safe fallback (confidence = 0.0, no visuals). Never render
 * a normal answer block next to a suspiciously empty confidence meter.
 */
import { CircleHelp } from 'lucide-react';

export function FallbackMessage() {
  return (
    <div className="message message--fallback">
      <CircleHelp size={16} aria-hidden="true" />
      <span>Couldn't produce a reliable answer for that</span>
    </div>
  );
}