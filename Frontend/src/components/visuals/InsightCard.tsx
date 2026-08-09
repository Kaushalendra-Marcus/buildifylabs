/**
 * InsightCard (F4) — `insight` visual (src/lib/schemas/visuals.ts). A single
 * insight statement with its supporting context beneath it.
 */
import { Lightbulb } from 'lucide-react';
import type { InsightProps } from '../../lib/schemas/visuals';

export function InsightCard({ props }: { props: InsightProps }) {
  const { text, context } = props;

  return (
    <div className="visual-insight">
      <p className="visual-insight__text">
        <Lightbulb size={14} aria-hidden="true" />
        {text}
      </p>
      <p className="visual-insight__context">{context}</p>
    </div>
  );
}
