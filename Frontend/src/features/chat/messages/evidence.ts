/**
 * Evidence helpers — pure derivations from `PipelineOutput` for the answer's
 * evidence stack (sources / process). No invented timings, no invented
 * sources: everything here reads fields the backend actually returns
 * (`sql_query`, `data_preview`, `visuals`, signal lists, `web_sources`).
 */
import type { PipelineOutput } from '../../../types/chat';

export interface DerivedSource {
  kind: 'your-data' | 'live-web';
  title: string;
  subtitle: string;
  detail: string | null;
  url: string | null;
}

/** Tables referenced by the executed SQL (`FROM` / `JOIN` clauses). */
export function tablesFromSql(sql: string | null): string[] {
  if (!sql) return [];
  const tables: string[] = [];
  const re = /\b(?:from|join)\s+([a-zA-Z0-9_".]+)/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(sql)) !== null) {
    const name = match[1].replace(/^["']|["']$/g, '');
    if (!tables.includes(name)) tables.push(name);
  }
  return tables.slice(0, 4);
}

export function deriveSources(output: PipelineOutput): DerivedSource[] {
  const sources: DerivedSource[] = [];

  const rows = output.data_preview ?? [];
  // An empty preview is no receipt: live-web answers carry `data_preview: []`
  // by construction, and must not show a bogus "your data" source.
  const hasReceipt = output.sql_query !== null || rows.length > 0;
  if (hasReceipt) {
    const tables = tablesFromSql(output.sql_query);
    const columns = rows.length > 0 ? Object.keys(rows[0]) : [];
    const rowBit = `${rows.length} row${rows.length === 1 ? '' : 's'}`;
    const colBit = columns.length > 0 ? ` · ${columns.slice(0, 4).join(', ')}` : '';
    sources.push({
      kind: 'your-data',
      title: tables.length > 0 ? tables.join(', ') : 'Connected data',
      subtitle: 'Your data',
      detail: `${rowBit}${colBit}`,
      url: null,
    });
  }

  for (const web of output.web_sources ?? []) {
    // Titles only — the heading links out; raw redirect URLs and the
    // provider tag stay out of the card body.
    let subtitle = '';
    const parsed = new Date(web.retrieved_at);
    if (!Number.isNaN(parsed.getTime())) {
      subtitle = `Retrieved ${parsed.toLocaleString()}`;
    }
    sources.push({
      kind: 'live-web',
      title: web.title,
      subtitle,
      detail: null,
      url: web.url,
    });
  }

  return sources;
}

export interface ProcessStep {
  label: string;
  detail: string;
}

export function deriveProcessSteps(output: PipelineOutput): ProcessStep[] {
  const rows = output.data_preview ?? [];
  const hasReceipt = output.sql_query !== null || output.data_preview !== null;
  const webCount = output.web_sources?.length ?? 0;
  const signals =
    output.insights.length + output.root_causes.length + output.recommendations.length;

  const visualBits = new Map<string, number>();
  for (const visual of output.visuals) {
    visualBits.set(visual.visual_type, (visualBits.get(visual.visual_type) ?? 0) + 1);
  }
  const visualDetail =
    output.visuals.length === 0
      ? 'no visuals'
      : [...visualBits.entries()]
          .map(([type, n]) => (n > 1 ? `${type} × ${n}` : type))
          .join(', ');

  if (!hasReceipt) {
    return [
      {
        label: 'Checking live web results',
        detail: `${webCount} source${webCount === 1 ? '' : 's'}`,
      },
      {
        label: 'Cross-checking evidence',
        detail: signals > 0 ? `${signals} signals` : 'no signals',
      },
      {
        label: `Generating ${output.visuals.length} visual card${output.visuals.length === 1 ? '' : 's'}`,
        detail: visualDetail,
      },
    ];
  }

  const tables = tablesFromSql(output.sql_query);
  const columns = rows.length > 0 ? Object.keys(rows[0]).length : 0;
  return [
    { label: 'Connecting to data source', detail: 'data source authenticated' },
    {
      label: 'Querying data',
      detail: tables.length > 0 ? tables.join(', ') : 'connected tables',
    },
    {
      label: 'SQL executed',
      detail: `${rows.length} rows · ${columns} cols`,
    },
    {
      label: 'Analyzing patterns',
      detail: signals > 0 ? `${signals} signals` : 'descriptive summary',
    },
    {
      label: `Generating ${output.visuals.length} visual card${output.visuals.length === 1 ? '' : 's'}`,
      detail: visualDetail,
    },
  ];
}
