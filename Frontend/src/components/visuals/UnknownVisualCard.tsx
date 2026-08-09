/**
 * UnknownVisualCard (F4) — defensive fallback for an unrecognized
 * `visual_type` (specs/14 §8: the backend's enum isn't guaranteed at
 * runtime). Degrades gracefully instead of crashing the message.
 */
import { HelpCircle } from 'lucide-react';
import type { VisualOutput } from '../../types/chat';

export function UnknownVisualCard({ visual }: { visual: VisualOutput }) {
  return (
    <div className="visual-unknown">
      <HelpCircle size={15} aria-hidden="true" />
      <p className="visual-unknown__text">This visual type isn't supported yet.</p>
      <p className="visual-unknown__type">{visual.visual_type}</p>
    </div>
  );
}
