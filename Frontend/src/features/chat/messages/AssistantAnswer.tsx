/**
 * AssistantAnswer (specs/14 §4.2) — the normal answer. A single left-aligned
 * block, NO bubble background (structured response, not a short chat turn):
 *   1. `answer` prose with `[n]` citation superscripts → source cards
 *   2. VisualCardsGrid — `repeat(auto-fit, minmax(240px, 1fr))`; graph/table
 *      span 2 columns (F3 lays the grid, F4 fills the cards)
 *   3. DataSources — expandable "· N sources" receipt (your-data + live web)
 *   4. ProcessTrace — expandable "· Process · N steps" (derived, no fake ms)
 *   5. InsightsStrip — collapsed "· Insights · N items", hedged copy
 *   6. TrustFooter — ALWAYS visible: show-the-query | confidence | flag
 *   7. News-context row — only when `news_context` is non-empty
 */
import { Newspaper } from 'lucide-react';
import type { PipelineOutput } from '../../../types/chat';
import { AssistantIdentity } from './AssistantIdentity';
import { AnswerProse } from './AnswerProse';
import { VisualCardsGrid } from './VisualCardsGrid';
import { DataSources } from './DataSources';
import { ProcessTrace } from './ProcessTrace';
import { InsightsStrip } from './InsightsStrip';
import { TrustFooter } from './TrustFooter';
import { deriveSources } from './evidence';

export function AssistantAnswer({
  output,
  answeredAt,
}: {
  output: PipelineOutput;
  answeredAt?: number;
}) {
  const sourceCount = deriveSources(output).length;

  return (
    <div className="message message--assistant-answer">
      <AssistantIdentity />
      <AnswerProse answer={output.answer} sourceCount={sourceCount} />

      <VisualCardsGrid visuals={output.visuals} />

      <DataSources output={output} />
      <ProcessTrace output={output} />

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
        answeredAt={answeredAt}
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
    </div>
  );
}
