/**
 * NoDataMessage (F6, specs/14 §6 / specs/07 edge case 2) — what a question
 * routes through when the user has no uploaded data: a distinct "add data
 * first" messaging, never a generic empty or a generic fallback.
 */
import { FileUp } from 'lucide-react';

export function NoDataMessage() {
  return (
    <div className="message message--no-data" role="status">
      <FileUp size={16} aria-hidden="true" />
      <span>
        You haven't uploaded any data yet — add a CSV, PDF, or spreadsheet to
        get started, then ask me a question about it.
      </span>
    </div>
  );
}
