/**
 * TrustFooter (4.2 step 4) — ALWAYS visible on a normal answer (never on
 * fallback/clarification), three affordances in one row (specs/10 §2):
 *   - "Show the query" — expands the SQL + raw data slice behind the answer
 *   - Confidence — a small labeled meter, rendered ONLY while the value is
 *     schema-bounded to 0..1 (specs/14 §4.2; never show a raw unbounded value)
 *   - "Flag this answer" — feeds `POST /chat/flag` → `QueryLogs`. Disabled
 *     with a tooltip (never hidden) when there is no query-log to flag.
 */
import { useState } from 'react';
import { Check, Code2, Flag } from 'lucide-react';
import { flagAnswer } from '../../../api/chat';

interface TrustFooterProps {
  queryLogId: string | null;
  sqlQuery: string | null;
  dataPreview: Array<Record<string, unknown>> | null;
  confidence: number;
}

function isBoundedConfidence(value: number): boolean {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

/** Compact raw-row slice — the "receipt" behind the answer (specs/10 §2). */
function DataPreview({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (rows.length === 0) {
    return <p className="trust-footer__query-note">No preview rows.</p>;
  }
  const columns = Object.keys(rows[0]);
  return (
    <div className="trust-footer__table-wrap">
      <table className="trust-footer__table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>{String(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TrustFooter({
  queryLogId,
  sqlQuery,
  dataPreview,
  confidence,
}: TrustFooterProps) {
  const [queryOpen, setQueryOpen] = useState(false);
  const [flagState, setFlagState] = useState<'idle' | 'flagging' | 'flagged' | 'error'>(
    'idle',
  );

  const showConfidence = isBoundedConfidence(confidence);
  const canFlag = Boolean(queryLogId);

  async function handleFlag() {
    if (!queryLogId || flagState === 'flagging' || flagState === 'flagged') {
      return;
    }
    setFlagState('flagging');
    try {
      await flagAnswer({ query_log_id: queryLogId });
      setFlagState('flagged');
    } catch {
      setFlagState('error');
    }
  }

  const hasQueryReceipt = sqlQuery !== null || dataPreview !== null;

  return (
    <div className="trust-footer">
      <button
        type="button"
        className="trust-footer__action"
        aria-expanded={queryOpen}
        onClick={() => setQueryOpen((value) => !value)}
      >
        <Code2 size={14} aria-hidden="true" />
        Show the query
      </button>

      {showConfidence && (
        <div className="trust-footer__confidence">
          <span className="trust-footer__confidence-label">Confidence</span>
          <div
            className="trust-footer__confidence-track"
            role="meter"
            aria-label="Confidence"
            aria-valuemin={0}
            aria-valuemax={1}
            aria-valuenow={confidence}
          >
            <div
              className="trust-footer__confidence-fill"
              style={{ width: `${confidence * 100}%` }}
            />
          </div>
          <span className="trust-footer__confidence-value">
            {Math.round(confidence * 100)}%
          </span>
        </div>
      )}

      <button
        type="button"
        className="trust-footer__action"
        disabled={!canFlag || flagState === 'flagged'}
        title={
          !canFlag
            ? "There is no query log for this answer, so it can't be flagged."
            : undefined
        }
        onClick={handleFlag}
      >
        {flagState === 'flagged' ? (
          <Check size={14} aria-hidden="true" />
        ) : (
          <Flag size={14} aria-hidden="true" />
        )}
        {flagState === 'flagged' ? 'Flagged' : 'Flag this answer'}
      </button>

      {flagState === 'error' && (
        <span className="trust-footer__error" role="alert">
          Couldn't flag this answer. Please try again.
        </span>
      )}

      {queryOpen && hasQueryReceipt && (
        <div className="trust-footer__query">
          {sqlQuery !== null && (
            <details className="trust-footer__details" open>
              <summary>SQL run for this answer</summary>
              <pre className="trust-footer__sql">
                <code>{sqlQuery}</code>
              </pre>
            </details>
          )}
          {dataPreview !== null && (
            <details className="trust-footer__details" open>
              <summary>Raw data slice this answer ran on</summary>
              <DataPreview rows={dataPreview} />
            </details>
          )}
        </div>
      )}
    </div>
  );
}