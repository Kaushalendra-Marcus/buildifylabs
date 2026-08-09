/**
 * HistoryRail (F2) — the collapsible chat-history rail (specs/14 §3): 0 /
 * 280px wide, collapsed by default below 768px and rendered as an OVERLAY on
 * narrow viewports (it never pushes the stream column). Contents numbered by
 * a later phase; the shell owns the open/closed + narrow-overlay behavior.
 */
interface HistoryRailProps {
  open: boolean;
}

export function HistoryRail({ open }: HistoryRailProps) {
  return (
    <aside
      className={`history-rail${open ? ' history-rail--open' : ' history-rail--closed'}`}
      aria-label="Chat history"
    >
      <div className="history-rail__body">
        <p className="history-rail__placeholder">Conversations will appear here.</p>
      </div>
    </aside>
  );
}