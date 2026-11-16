/** Chat / insight-pipeline contracts — mirror of docs/type-contracts.md §Chat.
 *  `visual_type`/`props` come from the frozen contract in
 *  `src/lib/schemas/visuals.ts` (the single source of truth). */

import { isVisualType, type VisualProps, type VisualType } from '../lib/schemas/visuals';

export type SourceScope = 'own_data' | 'live_web' | 'both';

export interface VisualOutput {
  /** Server-enforced `Literal[7]` since B4, but keep a defensive fallback for
   *  unrecognized values anyway (specs/14 §8). */
  visual_type: VisualType;
  /** Shape depends on visual_type — see src/lib/schemas/visuals.ts. */
  props: VisualProps;
  title: string;
  /** One-sentence takeaway shown directly under the title (InsightDeck rule:
   *  every chart explains its insight, not just its axes). Optional so older
   *  cached answers still render; the UI derives a data-backed fallback. */
  subtitle?: string | null;
  /** Visual provenance contract (backend first-class stage): intent,
   *  entities, metric, timeframe, source/computation IDs. Optional so
   *  older cached answers still validate; new answers always carry it. */
  provenance?: Record<string, unknown> | null;
}

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

function numberValue(value: unknown): number | null {
  if (isFiniteNumber(value)) return value;
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = Number(value.replace(/[$,%\s,]/g, ''));
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeVisual(raw: unknown): VisualOutput | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  const type = item.visual_type;
  if (!isVisualType(type)) return null;
  const props =
    item.props && typeof item.props === 'object'
      ? (item.props as Record<string, unknown>)
      : {};
  const title = typeof item.title === 'string' ? item.title.trim() : '';
  if (!title) return null;

  if (type === 'graph') {
    const chartType = props.chart_type;
    const validChartTypes = ['line', 'bar', 'pie', 'area'] as const;
    if (!validChartTypes.includes(chartType as (typeof validChartTypes)[number])) return null;
    const labels = Array.isArray(props.labels)
      ? props.labels.map(String)
      : [];
    const datasets = Array.isArray(props.datasets)
      ? props.datasets.flatMap((dataset) => {
          if (!dataset || typeof dataset !== 'object') return [];
          const value = dataset as Record<string, unknown>;
          const values = Array.isArray(value.values)
            ? value.values.map(numberValue)
            : [];
          if (!values.length || values.some((entry) => entry === null)) return [];
          return [{ name: String(value.name || 'Series'), values: values as number[] }];
        })
      : [];
    if (!labels.length || !datasets.length) return null;
    return {
      visual_type: type,
      title,
      props: { chart_type: chartType as (typeof validChartTypes)[number], labels, datasets },
      subtitle: typeof item.subtitle === 'string' ? item.subtitle : null,
      provenance: (item.provenance as Record<string, unknown>) ?? null,
    };
  }

  if (type === 'table') {
    const columns = Array.isArray(props.columns) ? props.columns.map(String) : [];
    const values = Array.isArray(props.values)
      ? props.values
          .filter((row): row is unknown[] => Array.isArray(row))
          .map((row) => row.slice(0, columns.length).map((cell) => {
            const numeric = numberValue(cell);
            return numeric !== null && typeof cell !== 'string' ? numeric : String(cell ?? '');
          }))
      : [];
    if (!columns.length || !values.length) return null;
    return {
      visual_type: type,
      title,
      props: { columns, values },
      subtitle: typeof item.subtitle === 'string' ? item.subtitle : null,
      provenance: (item.provenance as Record<string, unknown>) ?? null,
    };
  }

  const normalizedProps = { ...props };
  if (type === 'metric' || type === 'comparison') {
    for (const key of type === 'metric' ? ['value', 'change_pct'] : ['value', 'baseline']) {
      if (normalizedProps[key] !== null && numberValue(normalizedProps[key]) === null) {
        return null;
      }
      if (normalizedProps[key] !== null) normalizedProps[key] = numberValue(normalizedProps[key]);
    }
  }
  if (type === 'comparison' && Array.isArray(normalizedProps.groups)) {
    normalizedProps.groups = normalizedProps.groups.flatMap((group) => {
      if (!group || typeof group !== 'object') return [];
      const entry = group as Record<string, unknown>;
      const value = numberValue(entry.value);
      return value === null ? [] : [{ label: String(entry.label || 'Group'), value }];
    });
  }
  if (type === 'metric') {
    if (
      typeof normalizedProps.label !== 'string' ||
      typeof normalizedProps.value !== 'number' ||
      !['up', 'down', 'flat'].includes(String(normalizedProps.direction))
    ) return null;
  } else if (type === 'comparison') {
    if (
      typeof normalizedProps.value !== 'number' ||
      typeof normalizedProps.baseline !== 'number' ||
      !Array.isArray(normalizedProps.groups)
    ) return null;
  } else if (type === 'insight') {
    if (typeof normalizedProps.text !== 'string' || typeof normalizedProps.context !== 'string') return null;
  } else if (type === 'alert') {
    if (
      !['info', 'warning', 'critical'].includes(String(normalizedProps.level)) ||
      typeof normalizedProps.summary !== 'string' ||
      typeof normalizedProps.reason !== 'string'
    ) return null;
  } else if (type === 'status') {
    if (
      !['on_track', 'at_risk', 'off_track'].includes(String(normalizedProps.state)) ||
      typeof normalizedProps.detail !== 'string'
    ) return null;
  }
  return {
    visual_type: type,
    title,
    props: normalizedProps as unknown as VisualProps,
    subtitle: typeof item.subtitle === 'string' ? item.subtitle : null,
    provenance: (item.provenance as Record<string, unknown>) ?? null,
  };
}

export function normalizePipelineOutput(raw: unknown): PipelineOutput | null {
  if (!raw || typeof raw !== 'object') return null;
  const output = raw as Record<string, unknown>;
  if (typeof output.answer !== 'string') return null;
  const visuals = Array.isArray(output.visuals)
    ? output.visuals.flatMap((visual) => {
        const normalized = normalizeVisual(visual);
        return normalized ? [normalized] : [];
      })
    : [];
  return { ...output, visuals } as PipelineOutput;
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
