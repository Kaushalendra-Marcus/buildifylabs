/**
 * ProcessRunning — the in-flight PROCESSING card (amber, not purple).
 *
 * Shown while a question is being answered (`pending` is non-null and not
 * the cold-start state, which keeps its own named card). Completed steps get
 * a success check; the current step gets a spinner plus an amber
 * "running.." tail; later steps stay muted. Steps are honest about the
 * request kind — SQL-flavoured for your-data, retrieval-flavoured for live
 * web — with no invented millisecond timings. The assembling-blocks figure
 * (BoxLoader, Uiverse) docks at the side as ambient motion.
 */
import { Check, Loader2 } from 'lucide-react';
import { BoxLoader } from '../../../components/BoxLoader';

const OWN_DATA_STEPS = [
  'Connecting to data source',
  'Running SQL',
  'Analyzing patterns',
  'Generating visual cards',
];

const LIVE_WEB_STEPS = [
  'Searching the live web',
  'Checking evidence sufficiency',
  'Preparing answer',
];

/** Map a live server stage onto the step list (unknown stages pin the last).
 *  Server order is evidence -> judging -> narrating -> visuals. */
const SERVER_STAGE_ORDER = ['evidence', 'judging', 'narrating', 'visuals'];

function serverStageIndex(serverStage: string, stepCount: number): number {
  const position = SERVER_STAGE_ORDER.indexOf(serverStage);
  if (position === -1) return stepCount - 1;
  // Spread the four server stages across the visible steps.
  return Math.min(
    stepCount - 1,
    Math.floor((position / SERVER_STAGE_ORDER.length) * stepCount),
  );
}

export function ProcessRunning({
  liveWeb = false,
  stage = 'thinking',
  serverStage = null,
}: {
  liveWeb?: boolean;
  stage?: 'searching' | 'judging' | 'thinking';
  /** Live pipeline stage from the answer stream (`evidence`, `judging`,
   *  `narrating`, `visuals`). When present it drives the active step for
   *  real; otherwise the coarse local `stage` mapping applies. */
  serverStage?: string | null;
}) {
  const steps = liveWeb ? LIVE_WEB_STEPS : OWN_DATA_STEPS;
  const activeIndex =
    serverStage !== null && serverStage !== undefined
      ? serverStageIndex(serverStage, steps.length)
      : liveWeb
        ? stage === 'searching'
          ? 0
          : stage === 'judging'
            ? 1
            : 2
        : stage === 'thinking'
          ? 3
          : 1;
  const statusLabel = !liveWeb
    ? 'Assistant is thinking'
    : stage === 'searching'
      ? 'Searching the internet'
      : stage === 'judging'
        ? 'Checking whether the evidence is sufficient'
        : 'Using the verified results to prepare your answer';

  return (
    <div className="process-card process-card--running" role="status" aria-label={statusLabel}>
      <p className="process-card__eyebrow">Processing</p>
      <div className="process-card__body">
        <div className="process-card__steps">
          {steps.map((label, index) => {
            const done = index < activeIndex;
            const active = index === activeIndex;
            return (
              <div key={label} className="process-card__row">
                <span
                  className={[
                    'process-card__status',
                    done ? 'process-card__status--done' : '',
                    active ? 'process-card__status--active' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  aria-hidden="true"
                >
                  {done ? (
                    <Check size={13} />
                  ) : active ? (
                    <Loader2 size={13} className="process-card__spinner" />
                  ) : (
                    <span className="process-card__dot" />
                  )}
                </span>
                <span className="process-card__label">{label}</span>
                {active && <span className="process-card__running">running..</span>}
              </div>
            );
          })}
        </div>
        <BoxLoader size="sm" />
      </div>
    </div>
  );
}
