import { Fragment } from "react";
import type { LevelsSection } from "../api/client";

interface LevelsTableProps {
  levels: LevelsSection;
  expanded: boolean;
  onToggle: () => void;
}

// `LEVEL_HEADERS` has two empty header cells, unlike the grid's one — #88's single
// `aria-label` trick (`MandatePage.tsx`) doesn't generalise here, so each needs its own label,
// matching what `render.level_row` puts in that column.
const EMPTY_COLUMN_LABELS: Record<number, string> = {
  5: "invalidation or distance",
  7: "further levels",
};

export function LevelsTable({ levels, expanded, onToggle }: LevelsTableProps) {
  return (
    <section>
      <h2 className="mb-3 font-mono text-sm font-semibold text-ink">{levels.headline}</h2>
      {levels.groups.length === 0 ? (
        <p className="text-muted">{levels.empty_note}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              {levels.columns.map((column, i) => (
                <th key={i} aria-label={column === "" ? EMPTY_COLUMN_LABELS[i] : undefined}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {levels.groups.map((group) => (
              <Fragment key={group.label}>
                <tr className="group-row">
                  <td colSpan={levels.columns.length}>{group.label}</td>
                </tr>
                {(expanded ? group.rows : group.rows.slice(0, levels.shown)).map((row, i) => (
                  <tr key={`${group.label}-${i}`}>
                    {row.map((cell, j) => (
                      <td key={j}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {levels.withheld > 0 && (
        <button
          type="button"
          onClick={onToggle}
          className="mt-3 rounded-md border border-line bg-surface px-3 py-1 text-xs text-muted hover:bg-raised hover:text-ink"
        >
          {expanded ? "show fewer" : `show ${levels.withheld} more`}
        </button>
      )}
    </section>
  );
}
