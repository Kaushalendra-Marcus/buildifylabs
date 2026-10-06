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
  /** Visual provenance contract (backend first-class stage): intent,
   *  entities, metric, timeframe, source/computation IDs. Optional so
   *  older cached answers still validate; new answers always carry it. */
  provenance?: Record<string, unknown> | null;
}

/** Alternate response mode (specs/10 §2 "ask, don't guess") — non-null on a
 *  PipelineOutput means the other answer fields are empty: render as a
 *  quick-pick prompt, not a chat answer. */
export interface ClarificationRequest {
  question: string;
  options: string[]; // quick-pick choices; empty if none fit
}

export interface WebSource {
  title: string;
  url: string;
  provider: string;
  retrieved_at: string;
  /** Recency signal (YYYY-MM-DD) and provider relevance, when present.
   *  Optional so older cached rows still validate; mirrors backend WebSource. */
  published_date?: string | null;
  score?: number | null;
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
  web_sources?: WebSource[];
  anomalies: string[];
  confidence: number; // bounded 0..1 server-side (Field(ge=0.0, le=1.0))
  clarification: ClarificationRequest | null;
  sql_query: string | null; // exact SQL behind this answer (traceability)
  data_preview: Array<Record<string, unknown>> | null; // raw row slice
  query_log_id: string | null; // UUID — drives "show the query" + flagging
  thinking?: string[]; // machine-written pipeline steps (additive, may be absent)
  followups?: string[]; // tap-to-ask next questions (additive, may be absent)
  /** Compact structured research state (backend first-class stage).
   *  `scope_downgraded` drives the TrustFooter honesty notice; the rest is
   *  opaque to the UI. Optional so older cached answers still validate. */
  research_state?: {
    scope_requested?: SourceScope;
    scope_effective?: SourceScope;
    scope_downgraded?: boolean;
    thread_id?: string;
    [key: string]: unknown;
  } | null;
}

export interface ChatRequest {
  query: string;
  source_scope?: SourceScope; // only "own_data" fully supported today (B7 gated)
  company_name?: string | null; // reserved for benchmarking (specs/11)
  /** Per-conversation thread id — the active chat-store conversation id. */
  thread_id?: string;
  /** Document picker: restrict PDF evidence to these upload ids.
   *  Omitted/empty = all of the user's PDFs. */
  file_ids?: string[];
}

export interface FlagRequest {
  query_log_id: string;
}

export interface FlagResponse {
  query_log_id: string;
  flagged: boolean;
}
