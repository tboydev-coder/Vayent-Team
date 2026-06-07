import React from "react";

interface Props {
  columns: string[];
  rows: unknown[][];
}

const formatCell = (cell: unknown): string => {
  if (cell === null || cell === undefined) {
    return "";
  }

  if (typeof cell === "object") {
    return JSON.stringify(cell);
  }

  return String(cell);
};

const QueryResultTable: React.FC<Props> = ({ columns, rows }) => {
  return (
    <table className="min-w-full bg-surface">
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col} className="px-2 py-1 text-left" title={col}>
              {col}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, idx) => (
          <tr key={idx} className="border-t">
            {r.map((cell, i) => (
              <td key={i} className="px-2 py-1" title={formatCell(cell)}>
                {formatCell(cell)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
};

export default QueryResultTable;
