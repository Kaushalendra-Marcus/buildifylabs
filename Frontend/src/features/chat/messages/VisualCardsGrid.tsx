/**
 * VisualCardsGrid (4.2 step 2) — the inline visual card layout rule from
 * specs/14 §4.2: `repeat(auto-fit, minmax(240px, 1fr))`, with `graph` and
 * `table` spanning two columns.
 *
 * F3 lays the GRID (title + `data-visual-type` + wide-span classes);
 * F4 fills each card with the real per-`visual_type` component via the
 * plain type→component lookup in `VisualCard`.
 *
 * InsightDeck layer: each card is a figure with a readable title and a
 * one-sentence insight subtitle, and the whole answer can open in a
 * read-only presentation deck.
 */
import { useState } from 'react';
import { Play } from 'lucide-react';
import type { VisualOutput } from '../../../types/chat';
import { VisualCard } from '../../../components/visuals/VisualCard';
import { visualSubtitle } from '../../../components/visuals/visual-meta';
import { PresentationDeck } from './PresentationDeck';

const WIDE_VISUAL_TYPES = new Set(['graph', 'table']);

export function VisualCardsGrid({ visuals }: { visuals: VisualOutput[] }) {
  const [presenting, setPresenting] = useState(false);

  if (visuals.length === 0) return null;

  return (
    <div className="visual-deck">
      <div className="visual-deck__toolbar">
        <p className="visual-deck__label">Visual analysis</p>
        <button
          type="button"
          className="visual-deck__present"
          onClick={() => setPresenting(true)}
          aria-haspopup="dialog"
          aria-label={`Present ${visuals.length} visual${visuals.length === 1 ? '' : 's'}`}
        >
          <Play size={13} aria-hidden="true" fill="currentColor" />
          Present
        </button>
      </div>

      <div className="visual-cards-grid" role="group" aria-label="Visual results">
        {visuals.map((visual, index) => {
          const isWide = WIDE_VISUAL_TYPES.has(visual.visual_type);
          const subtitle = visualSubtitle(visual);
          return (
            <figure
              key={`${visual.visual_type}-${index}`}
              className={[
                'visual-cards-grid__card',
                isWide ? 'visual-cards-grid__card--wide' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              data-visual-type={visual.visual_type}
            >
              <figcaption className="visual-cards-grid__caption">
                <p className="visual-cards-grid__title">{visual.title}</p>
                {subtitle && (
                  <p className="visual-cards-grid__subtitle">{subtitle}</p>
                )}
              </figcaption>
              <VisualCard visual={visual} />
            </figure>
          );
        })}
      </div>

      {presenting && (
        <PresentationDeck
          visuals={visuals}
          onClose={() => setPresenting(false)}
        />
      )}
    </div>
  );
}
