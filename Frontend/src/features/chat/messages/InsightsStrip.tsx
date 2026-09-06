/**
 * InsightsStrip (4.2 step 3) — collapsed by default, expandable. Groups
 * `insights[]`, `root_causes[]`, `recommendations[]` under a hedged
 * "Possible factors" label — never "Why this happened" (specs/10 §2 causal
 * language rule; specs/14 §4.2 "Possible factors" heading). Supporting text
 * is secondary/muted, never primary: it accompanies the answer, it isn't it.
 *
 * Toggle reads "· Insights · N items" to sit in the answer's evidence stack
 * (sources / process / insights), amber accent only.
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

  const total = insights.length + rootCauses.length + recommendations.length;
  if (total === 0) return null;

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
          Insights · {total} item{total === 1 ? '' : 's'}
        </span>
      </button>

      {open && (
        <div className="insights-strip">
          <p className="insights-strip__lede">Possible factors</p>
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
        </div>
      )}
    </div>
  );
}
