/**
 * VisualCardsGrid (4.2 step 2) — the inline visual card layout rule from
 * specs/14 §4.2: `repeat(auto-fit, minmax(240px, 1fr))`, with `graph` and
 * `table` spanning two columns.
 *
 * F3 lays the GRID (title + `data-visual-type` + wide-span classes);
 * F4 fills each card with the real per-`visual_type` component via the
 * plain type→component lookup in `VisualCard`.
 */
import type { VisualOutput } from '../../../types/chat';
import { VisualCard } from '../../../components/visuals/VisualCard';

const WIDE_VISUAL_TYPES = new Set(['graph', 'table']);

export function VisualCardsGrid({ visuals }: { visuals: VisualOutput[] }) {
  if (visuals.length === 0) return null;

  return (
    <div className="visual-cards-grid" aria-label="Visual results">
      {visuals.map((visual, index) => {
        const isWide = WIDE_VISUAL_TYPES.has(visual.visual_type);
        return (
          <div
            key={`${visual.visual_type}-${index}`}
            className={[
              'visual-cards-grid__card',
              isWide ? 'visual-cards-grid__card--wide' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            data-visual-type={visual.visual_type}
          >
            <p className="visual-cards-grid__title">{visual.title}</p>
            <VisualCard visual={visual} />
          </div>
        );
      })}
    </div>
  );
}