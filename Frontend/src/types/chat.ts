/** Chat / insight-pipeline contracts — mirror of docs/type-contracts.md §Chat.
 *  `visual_type`/`props` come from the frozen contract in
 *  `src/lib/schemas/visuals.ts` (the single source of truth). */

import type { VisualProps, VisualType } from '../lib/schemas/visuals';

export type SourceScope = 'own_data' | 'live_web' | 'both';

export interface VisualOutput {
  /** Server-enforced `Literal[7]` since B4, but keep a defensive fallback for
   *  unrecognized values anyway (specs/14 §8). */
  visual_type: VisualType;
  /** Shape depends on visual_type — see src/lib/schemas/visuals.ts. */
  props: VisualProps;
  title: string;
}

/** Alternate response mode (specs/10 §2 "ask, don't guess") — non-null on a
 *  PipelineOutput means the other answer fields are empty: render as a
 *  quick-pick prompt, not a chat answer. */
export interface ClarificationRequest {
  question: string;
  options: string[]; // quick-pick choices; empty if none fit
}

/** POST /chat returns this directly. */
export interface PipelineOutput {
  answer: string;
  visuals: VisualOutput[];
  insights: string[];
  summary: string;
  root_causes: string[]; // hedged causal language by design (specs/10 §2)
  recommendations: string[];
  news_context: string[]; // empty until specs/07
  anomalies: string[];
  confidence: number; // bounded 0..1 server-side (Field(ge=0.0, le=1.0))
  clarification: ClarificationRequest | null;
  sql_query: string | null; // exact SQL behind this answer (traceability)
  data_preview: Array<Record<string, unknown>> | null; // raw row slice
  query_log_id: string | null; // UUID — drives "show the query" + flagging
}

export interface ChatRequest {
  query: string;
  source_scope?: SourceScope; // only "own_data" fully supported today (B7 gated)
  company_name?: string | null; // reserved for benchmarking (specs/11)
}

export interface FlagRequest {
  query_log_id: string;
}

export interface FlagResponse {
  query_log_id: string;
  flagged: boolean;
}
