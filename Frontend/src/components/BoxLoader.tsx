/**
 * BoxLoader — the Uiverse 3D assembling-blocks figure (Admin12121),
 * namespaced to `.bl-boxloader*` (the original's bare `.loader`/`.box`
 * classes would collide with app styles) and re-skinned to the workspace
 * intelligence palette: amber blocks (#ffbf48 → #e89b2e) on the dark card
 * surface. Pure CSS, no JS.
 */
import './BoxLoader.css';

const BOXES = [0, 1, 2, 3, 4, 5, 6, 7];

export function BoxLoader({
  label = 'Assembling answer blocks',
  size = 'md',
}: {
  label?: string;
  size?: 'md' | 'sm';
}) {
  return (
    <div
      className={`bl-boxloader${size === 'sm' ? ' bl-boxloader--sm' : ''}`}
      role="img"
      aria-label={label}
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
