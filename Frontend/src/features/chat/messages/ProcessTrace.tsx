/**
 * ProcessTrace — the expandable "· Process · N steps" evidence line.
 *
 * Steps are DERIVED from fields the backend actually returns (see
 * `./evidence`) — no invented millisecond timings. Amber accent only.
 */
import { useState } from 'react';
import { Check, ChevronDown, ChevronRight } from 'lucide-react';
import type { PipelineOutput } from '../../../types/chat';
import { deriveProcessSteps } from './evidence';

export function ProcessTrace({ output }: { output: PipelineOutput }) {
  const [open, setOpen] = useState(false);
  const steps = deriveProcessSteps(output);

  return (
    <div className="evidence-block">
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown size={13} aria-hidden="true" />
        ) : (
          <ChevronRight size={13} aria-hidden="true" />
        )}
        <span aria-hidden="true">·</span>
        <span>
          Process · {steps.length} step{steps.length === 1 ? '' : 's'}
        </span>
      </button>

      {open && (
        <div className="process-card" role="list" aria-label="Process steps">
          {steps.map((step) => (
            <div key={step.label} className="process-card__row" role="listitem">
              <span className="process-card__status" aria-hidden="true">
                <Check size={13} />
              </span>
              <span className="process-card__label">{step.label}</span>
              <span className="process-card__detail">{step.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
