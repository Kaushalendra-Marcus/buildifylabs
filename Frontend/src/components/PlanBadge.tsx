/**
 * PlanBadge (F1) — renders the user's plan from `AuthResponse.user.plan`
 * (`guest|free|pro`, specs/01 §3). Unknown future values degrade to their
 * capitalized name rather than rendering nothing.
 */
import type { Plan } from '../types';
import './PlanBadge.css';

interface PlanBadgeProps {
  plan: Plan;
}

const FALLBACK_PLAN: Plan = 'free';

export function PlanBadge({ plan }: PlanBadgeProps) {
  const value: Plan = plan === 'guest' || plan === 'pro' || plan === 'free'
    ? plan
    : FALLBACK_PLAN;

  return <span className={`plan-badge plan-badge--${value}`}>{value}</span>;
}
