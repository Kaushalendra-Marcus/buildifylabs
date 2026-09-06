/**
 * AssistantAnswer (specs/14 §4.2) — the normal answer. A single left-aligned
 * block, NO bubble background (structured response, not a short chat turn):
 *   1. `answer` prose (full width)
 *   2. VisualCardsGrid — `repeat(auto-fit, minmax(240px, 1fr))`; graph/table
 *      span 2 columns (F3 lays the grid, F4 fills the cards)
 *   3. InsightsStrip — collapsed by default, hedged "Possible factors"
 *   4. TrustFooter — ALWAYS visible: show-the-query | confidence | flag
 *   5. News-context row — only when `news_context` is non-empty
 */
import { ExternalLink, Newspaper } from 'lucide-react';
import type { PipelineOutput } from '../../../types/chat';
import { VisualCardsGrid } from './VisualCardsGrid';
import { InsightsStrip } from './InsightsStrip';
import { TrustFooter } from './TrustFooter';

export function AssistantAnswer({ output }: { output: PipelineOutput }) {
  return (
    <div className="message message--assistant-answer">
      <p className="message__answer-prose">{output.answer}</p>

      <VisualCardsGrid visuals={output.visuals} />

      <InsightsStrip
        insights={output.insights}
        rootCauses={output.root_causes}
        recommendations={output.recommendations}
      />

      <TrustFooter
        queryLogId={output.query_log_id}
        sqlQuery={output.sql_query}
        dataPreview={output.data_preview}
        confidence={output.confidence}
      />

      {output.news_context.length > 0 && (
        <div className="news-context-row">
          <p className="news-context-row__label">
            <Newspaper size={13} aria-hidden="true" />
            From the web
          </p>
          <ul className="news-context-row__list">
            {output.news_context.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {output.web_sources && output.web_sources.length > 0 && (
        <div className="web-sources" aria-label="Web sources">
          <p className="web-sources__heading">
            <Newspaper size={13} aria-hidden="true" />
            Sources checked while answering
          </p>
          <ul className="web-sources__list">
            {output.web_sources.map((source) => (
              <li key={`${source.url}-${source.retrieved_at}`}>
                <a href={source.url} target="_blank" rel="noreferrer">
                  {source.title}
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
                <span>{source.provider} · {new Date(source.retrieved_at).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}