/**
 * BusinessSummaryTable (F4) — `table` visual (src/lib/schemas/visuals.ts).
 * Always a real table: header row with clear column names, body rows with
 * zebra + hover, wrapped cells (no horizontal-scroll clipping). Spans two
 * columns in the grid (specs/14 §4.2).
 *
 * Column pairs from the backend keep their clear names:
 * - ["Figure", "Context"] → figure cell in accent, context tidied (snippet
 *   windows can leak markdown "||"/"####", marked with "…" where cut).
 * - ["Date", "Event"]     → date cell in mono, event tidied the same way.
 * - anything else         → generic table with numeric right-alignment.
 */
import type { TableProps } from '../../lib/schemas/visuals';

/** "18.9% [2]" → { value: "18.9%", ref: "2" }; falls back to raw text. */
function splitFigureCited(cell: string): { value: string; ref: string | null } {
  const match = /^(.*?)\s*\[(\d+)\]\s*$/.exec(cell.trim());
  if (match) return { value: match[1].trim() || cell.trim(), ref: match[2] };
  return { value: cell.trim(), ref: null };
}

/** "Event text [3]" → { text, ref }; falls back to raw text. */
function splitEventCited(cell: string): { text: string; ref: string | null } {
  const match = /^(.*?)\s*\[(\d+)\]\s*$/.exec(cell.trim());
  if (match) return { text: match[1].trim() || cell.trim(), ref: match[2] };
  return { text: cell.trim(), ref: null };
}

/** Compact ISO dates; anything else passes through. */
function shortDate(raw: string): string {
  const text = raw.trim();
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  if (iso) {
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    const month = months[Number(iso[2]) - 1] ?? iso[2];
    return `${month} ${Number(iso[3])}, ${iso[1]}`;
  }
  return text;
}

/** Strip markdown/table junk that leaks from raw web snippets, and mark
 *  mid-sentence cuts with "…" so a sliced window reads as a quote fragment
 *  instead of a typo ("b outlook" → "…outlook"). */
function cleanProse(raw: string): string {
  let text = String(raw ?? '').replace(/\s+/g, ' ');
  text = text.replace(/\[[^\]]*\]/g, ' '); // [...] / stray [n] fragments
  text = text.replace(/#+\s*/g, ''); // markdown headings
  text = text.replace(/\|+/g, ' · '); // table pipes → separator
  text = text.replace(/(·\s*){2,}/g, '· ');
  text = text.replace(/[-–—]{2,}/g, ' – ');
  text = text.replace(/\*{1,2}([^*]+?)\*{1,2}/g, '$1');
  text = text.replace(/\s{2,}/g, ' ').trim();
  // Leading 1–2 char stub from a mid-word cut ("b outlook", "le Energy").
  text = text.replace(/^[A-Za-z]{1,2}\s+(?=[A-Za-z])/, '');
  if (/^[a-z]/.test(text)) text = `…${text}`;
  if (text && !/[.!?…:;]$/.test(text)) text = `${text} …`;
  return text;
}

export function BusinessSummaryTable({ props }: { props: TableProps }) {
  const { columns, values } = props;

  if (values.length === 0) {
    return <p className="visual-table__empty">No rows to show.</p>;
  }

  const normalized = columns.map((column) => column.toLowerCase().trim());
  const isFigures =
    normalized.length === 2 &&
    normalized[0] === 'figure' &&
    normalized[1] === 'context';
  const isTimeline =
    normalized.length === 2 &&
    normalized[0] === 'date' &&
    normalized[1] === 'event';

  // Generic table: detect numeric columns once for right-alignment.
  const numericColumn = columns.map((_, columnIndex) =>
    values.every((row) => {
      const cell = row[columnIndex];
      return (
        typeof cell === 'number' ||
        (typeof cell === 'string' &&
          cell.trim() !== '' &&
          !Number.isNaN(Number(cell.replace(/[,$%]/g, ''))))
      );
    }),
  );

  return (
    <div className="visual-table-wrap">
      <table className="visual-table">
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
          {values.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => {
                if (isFigures && cellIndex === 0) {
                  const { value, ref } = splitFigureCited(String(cell));
                  return (
                    <td
                      key={cellIndex}
                      className="visual-table__key visual-table__figure"
                      title={String(cell)}
                    >
                      {value}
                      {ref !== null && (
                        <span className="visual-table__ref"> [{ref}]</span>
                      )}
                    </td>
                  );
                }
                if (isFigures && cellIndex === 1) {
                  return (
                    <td key={cellIndex} className="visual-table__prose">
                      {cleanProse(String(cell))}
                    </td>
                  );
                }
                if (isTimeline && cellIndex === 0) {
                  return (
                    <td
                      key={cellIndex}
                      className="visual-table__key visual-table__date"
                    >
                      {shortDate(String(cell))}
                    </td>
                  );
                }
                if (isTimeline && cellIndex === 1) {
                  const { text, ref } = splitEventCited(String(cell));
                  return (
                    <td key={cellIndex} className="visual-table__prose">
                      {cleanProse(text)}
                      {ref !== null && (
                        <a
                          className="answer-cite"
                          href={`#source-${ref}`}
                          aria-label={`Source ${ref}`}
                        >
                          {ref}
                        </a>
                      )}
                    </td>
                  );
                }
                return (
                  <td
                    key={cellIndex}
                    className={
                      numericColumn[cellIndex]
                        ? 'visual-table__num'
                        : undefined
                    }
                  >
                    {cell}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="visual-table__count">
        {values.length} row{values.length === 1 ? '' : 's'}
      </p>
    </div>
  );
}
