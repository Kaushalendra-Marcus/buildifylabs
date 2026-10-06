/**
 * DataSources — the expandable "· N sources" evidence line under an answer.
 *
 * Sources are DERIVED from fields the backend actually returns today — never
 * invented (see `./evidence`). Numbered 1-based so `AnswerProse` `[n]`
 * citations have a landing target (`#source-n`). Amber accent only.
 *
 * Citation/provenance mapping: prose citations are 1-based card numbers
 * (`[n]` → `#source-n`), while backend visual provenance uses 0-based
 * evidence ids (`web:<i>` / `doc:<i>` = index into `web_sources`,
 * `rows` = your-data receipt, `computed:forecast` = deterministic
 * projection). Each card carries its provenance id as `data-provenance-id`
 * so the two schemes resolve to the same card without renumbering either.
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight, Database, FileText, Globe } from 'lucide-react';
import type { PipelineOutput } from '../../../types/chat';
import { deriveSources } from './evidence';

export function DataSources({ output }: { output: PipelineOutput }) {
  const [open, setOpen] = useState(false);
  const sources = deriveSources(output);
  if (sources.length === 0) return null;

  const yourDataCount = sources.filter((s) => s.kind === 'your-data').length;
  const yourDocumentsCount = sources.filter((s) => s.kind === 'your-documents').length;
  const liveWebCount = sources.length - yourDataCount - yourDocumentsCount;
  // `deriveSources` emits the your-data receipt first (when present), then
  // `web_sources` in order — so a web/doc card's 0-based provenance index is
  // its card position minus the leading receipt offset.
  const receiptOffset = yourDataCount > 0 ? 1 : 0;

  function provenanceIdFor(source: (typeof sources)[number], index: number): string {
    if (source.kind === 'your-data') return 'rows';
    const webIndex = index - receiptOffset;
    return source.kind === 'your-documents' ? `doc:${webIndex}` : `web:${webIndex}`;
  }

  return (
    <div className="evidence-block">
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown size={13} aria-hidden="true" />
        ) : (
          <ChevronRight size={13} aria-hidden="true" />
        )}
        <span aria-hidden="true">·</span>
        <span>
          {sources.length} source{sources.length === 1 ? '' : 's'}
        </span>
        {yourDataCount > 0 && (
          <span className="evidence-badge evidence-badge--own">
            {yourDataCount} your data
          </span>
        )}
        {yourDocumentsCount > 0 && (
          <span className="evidence-badge evidence-badge--documents">
            {yourDocumentsCount} your documents
          </span>
        )}
        {liveWebCount > 0 && (
          <span className="evidence-badge evidence-badge--web">
            {liveWebCount} live web
          </span>
        )}
      </button>

      {open && (
        <ol className="sources-list">
          {sources.map((source, index) => (
            <li
              key={`${source.kind}-${index}`}
              id={`source-${index + 1}`}
              className="source-card"
              data-provenance-id={provenanceIdFor(source, index)}
            >
              <span className="source-card__number" aria-hidden="true">
                {index + 1}
              </span>
              <div className="source-card__body">
                <p className="source-card__title">
                  {source.kind === 'your-data' ? (
                    <Database size={13} aria-hidden="true" />
                  ) : source.kind === 'your-documents' ? (
                    <FileText size={13} aria-hidden="true" />
                  ) : (
                    <Globe size={13} aria-hidden="true" />
                  )}
                  {source.url ? (
                    <a href={source.url} target="_blank" rel="noreferrer">
                      {source.title}
                    </a>
                  ) : (
                    source.title
                  )}
                </p>
                {source.subtitle && (
                  <p className="source-card__subtitle">{source.subtitle}</p>
                )}
                {source.detail && (
                  <p className="source-card__detail">{source.detail}</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
