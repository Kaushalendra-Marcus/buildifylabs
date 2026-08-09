/**
 * InsightsStrip (4.2 step 3) — collapsed by default, expandable. Groups
 * `insights[]`, `root_causes[]`, `recommendations[]` under a hedged
 * "Possible factors" label — never "Why this happened" (specs/10 §2 causal
 * language rule; specs/14 §4.2 "Possible factors" heading). Supporting text
 * is secondary/muted, never primary: it accompanies the answer, it isn't it.
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

interface InsightsStripProps {
  insights: string[];
  rootCauses: string[];
  recommendations: string[];
}

export function InsightsStrip({
  insights,
  rootCauses,
  recommendations,
}: InsightsStripProps) {
  const [open, setOpen] = useState(false);

  const hasContent =
    insights.length > 0 || rootCauses.length > 0 || recommendations.length > 0;

  if (!hasContent) return null;

  return (
    <div className="insights-strip">
      <button
        type="button"
        className="insights-strip__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown size={14} aria-hidden="true" />
        ) : (
          <ChevronRight size={14} aria-hidden="true" />
        )}
        Possible factors
      </button>

      {open && (
        <div className="insights-strip__body">
          {rootCauses.length > 0 && (
            <section className="insights-strip__group">
              <h4 className="insights-strip__heading">
                Possible factors to consider
              </h4>
              <ul className="insights-strip__list">
                {rootCauses.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
          {recommendations.length > 0 && (
            <section className="insights-strip__group">
              <h4 className="insights-strip__heading">Potential next steps</h4>
              <ul className="insights-strip__list">
                {recommendations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
          {insights.length > 0 && (
            <section className="insights-strip__group">
              <h4 className="insights-strip__heading">Supporting signals</h4>
              <ul className="insights-strip__list">
                {insights.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}