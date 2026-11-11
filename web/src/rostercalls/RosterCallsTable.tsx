import { Fragment } from "react";
import type { RosterCallsSection } from "../api/client";

interface RosterCallsTableProps {
  calls: RosterCallsSection;
  expanded: boolean;
  onToggle: () => void;
}

const EMPTY_COLUMN_LABELS: Record<number, string> = {
  0: "direction",
};

export function RosterCallsTable({ calls, expanded, onToggle }: RosterCallsTableProps) {
  return (
    <section>
      <h2 className="mb-3 font-mono text-sm font-semibold text-ink">{calls.headline}</h2>
      {calls.holdings.length === 0 ? (
        calls.empty_note && <p className="text-muted">{calls.empty_note}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              {calls.columns.map((column, i) => (
                <th key={i} aria-label={column === "" ? EMPTY_COLUMN_LABELS[i] : undefined}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {calls.holdings.map((holding) => (
              <Fragment key={holding.title}>
                <tr className="group-row">
                  <td colSpan={calls.columns.length}>{holding.title}</td>
                </tr>
                {holding.rows
                  .filter((row) => expanded || row.headline)
                  .map((row, i) => (
                    <tr key={`${holding.title}-${i}`}>
                      {row.cells.map((cell, j) => (
                        <td key={j}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                {holding.dropped > 0 && (
                  <tr className="note-row">
                    <td colSpan={calls.columns.length}>{`${holding.dropped} out of band`}</td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {calls.out_of_band_only && <p className="mt-3 text-xs text-muted">{calls.out_of_band_only}</p>}
      {calls.other_scale && <p className="mt-3 text-xs text-muted">{calls.other_scale}</p>}
      {calls.withheld > 0 && (
        <button
          type="button"
          onClick={onToggle}
          className="mt-3 rounded-md border border-line bg-surface px-3 py-1 text-xs text-muted hover:bg-raised hover:text-ink"
        >
          {expanded ? "show fewer" : `show ${calls.withheld} more`}
        </button>
      )}
    </section>
  );
}
