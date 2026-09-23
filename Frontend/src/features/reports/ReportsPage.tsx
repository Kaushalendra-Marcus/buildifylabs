/**
 * ReportsPage — pinned charts (`/app/reports`). Reads the frontend-only
 * reports-store (filled by "Pin to reports" in the chat trust footer) and
 * renders each pinned answer with the same VisualCard lookup the chat uses.
 */
import { Link } from 'react-router-dom';
import { Pin } from 'lucide-react';
import { VisualCard } from '../../components/visuals/VisualCard';
import { useReportsStore } from './reports-store';
import './reports.css';

export function ReportsPage() {
  const reports = useReportsStore((s) => s.reports);
  const unpinReport = useReportsStore((s) => s.unpinReport);

  return (
    <div className="reports-page" role="main" aria-label="Pinned reports">
      <section className="reports-page__head">
        <div>
          <p className="reports-page__eyebrow">Reports</p>
          <h1 className="reports-page__title">Pinned reports</h1>
          <p className="reports-page__sub">
            Pin any chat answer and it lives here — your dashboard memory.
          </p>
        </div>
        <Link to="/app/chat" className="reports-page__ghost">
          Back to chat
        </Link>
      </section>

      {reports.length === 0 ? (
        <p className="reports-page__empty">
          Nothing pinned yet. Open a chat answer and use “Pin to reports”
          under it.
        </p>
      ) : (
        <ul className="reports-page__grid">
          {reports.map((report) => (
            <li key={report.id} className="reports-page__card">
              <div className="reports-page__card-head">
                <p className="reports-page__card-title">
                  <Pin size={14} aria-hidden="true" />
                  {report.title}
                </p>
                <button
                  type="button"
                  className="reports-page__unpin"
                  onClick={() => unpinReport(report.id)}
                  aria-label={`Unpin ${report.title}`}
                >
                  Unpin
                </button>
              </div>
              <p className="reports-page__answer">
                {report.answer.slice(0, 220)}
                {report.answer.length > 220 ? '…' : ''}
              </p>
              {report.output.visuals.length > 0 && (
                <div className="reports-page__visuals">
                  {report.output.visuals.map((visual, index) => (
                    <div
                      key={`${visual.visual_type}-${index}`}
                      className="reports-page__visual"
                      data-visual-type={visual.visual_type}
                    >
                      <p className="reports-page__visual-title">{visual.title}</p>
                      <VisualCard visual={visual} />
                    </div>
                  ))}
                </div>
              )}
              <p className="reports-page__meta">
                Pinned {new Date(report.pinnedAt).toLocaleString()}
                {Number.isFinite(report.output.confidence)
                  ? ` · Confidence ${Math.round(report.output.confidence * 100)}%`
                  : ''}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
