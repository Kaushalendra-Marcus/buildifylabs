/**
 * GooLoader — the gooey amber orb loader (Uiverse.io by andrew-manzyk),
 * shared by the landing page decoration and the /app loading screen.
 *
 * Markup, variables, ring, gradient box, the 7 mask polygons, and the
 * rotation/roundness motion are verbatim from the author's snippet. Three
 * deliberate adaptations, nothing else:
 *  - Scoped to `.goo-loader` (the original `.loader` would collide) with
 *    prefixed keyframes.
 *  - The mask id is unique per instance (the page renders several loaders;
 *    duplicate ids would all resolve to the first mask). The box's
 *    `mask-image` is set inline to that id.
 *  - The author's `colorize` hue-rotate animation is omitted: on this amber
 *    pair it cycles through magenta/purple, and this codebase has a standing
 *    no-purple rule. The gooey motion is untouched.
 */
import { useId } from 'react';
import type { CSSProperties } from 'react';
import './GooLoader.css';

interface GooLoaderProps {
  /** Accessible label. Omit for a purely decorative instance (aria-hidden). */
  label?: string;
  /** Scale multiplier for the 100px original (`--size` in the snippet). */
  size?: number;
}

export function GooLoader({ label, size = 1 }: GooLoaderProps) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '');
  const maskId = `goo-clipping-${uid}`;

  return (
    <div
      className="goo-loader"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ '--goo-size': size } as CSSProperties}
    >
      <svg width="100" height="100" viewBox="0 0 100 100" aria-hidden="true">
        <defs>
          <mask id={maskId}>
            <polygon points="0,0 100,0 100,100 0,100" fill="black" />
            <polygon points="25,25 75,25 50,75" fill="white" />
            <polygon points="50,25 75,75 25,75" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
            <polygon points="35,35 65,35 50,65" fill="white" />
          </mask>
        </defs>
      </svg>
      <div
        className="goo-loader__box"
        aria-hidden="true"
        style={{
          maskImage: `url(#${maskId})`,
          WebkitMaskImage: `url(#${maskId})`,
        }}
      />
    </div>
  );
}
