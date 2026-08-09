/**
 * VisualCardsGrid (4.2 step 2) — the inline visual card layout rule from
 * specs/14 §4.2: `repeat(auto-fit, minmax(240px, 1fr))`, with `graph` and
 * `table` spanning two columns.
 *
 * F3 lays the GRID and renders a placeholder card per visual (title + type);
 * F4 replaces the placeholder content with the real per-`visual_type`
 * renderers via its plain type→component lookup. The `data-visual-type`
 * attribute and the definitive span class are the seams F4 consumes.
 */
import type { VisualOutput } from '../../../types/chat';

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
            {/* F4 renders the per-type component inside the card. */}
          </div>
        );
      })}
    </div>
  );
}