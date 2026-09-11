/**
 * BoxLoader — the Uiverse 3D assembling-blocks figure (Admin12121),
 * namespaced to `.bl-boxloader*` (the original's bare `.loader`/`.box`
 * classes would collide with app styles) and re-skinned to the workspace
 * intelligence palette: amber blocks (#ffbf48 → #e89b2e) on the card
 * surface. Pure CSS, no JS.
 */
import type { CSSProperties } from 'react';
import './BoxLoader.css';

const BOXES = [0, 1, 2, 3, 4, 5, 6, 7];

export function BoxLoader({
  label,
  size = 'md',
  tone = 'card',
}: {
  /** Accessible label. Omit for a purely decorative instance (aria-hidden). */
  label?: string;
  size?: 'md' | 'sm';
  /** Surface behind the figure — the mask curtains must match it exactly. */
  tone?: 'card' | 'page';
}) {
  return (
    <div
      className={`bl-boxloader${size === 'sm' ? ' bl-boxloader--sm' : ''}`}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={
        {
          '--bl-box-mask': tone === 'page' ? 'var(--surface-page)' : undefined,
        } as CSSProperties
      }
    >
      {BOXES.map((box) => (
        <div
          key={box}
          className={`bl-boxloader__box bl-boxloader__box--${box}`}
          aria-hidden="true"
        >
          <div />
        </div>
      ))}
      <div className="bl-boxloader__ground" aria-hidden="true">
        <div />
      </div>
    </div>
  );
}
