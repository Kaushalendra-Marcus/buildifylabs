/**
 * BusinessSummaryTable (F4) — `table` visual (src/lib/schemas/visuals.ts). A
 * real table of the raw columns/values the backend returned; spans two
 * columns in the grid (specs/14 §4.2).
 */
import type { TableProps } from '../../lib/schemas/visuals';

export function BusinessSummaryTable({ props }: { props: TableProps }) {
  const { columns, values } = props;

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
              {row.map((cell, cellIndex) => (
                <td key={cellIndex}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
