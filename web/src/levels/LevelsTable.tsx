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
    <div>
      <p>{levels.headline}</p>
      {levels.groups.length === 0 ? (
        <p>{levels.empty_note}</p>
      ) : (
        <table>
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
                <tr>
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
        <button type="button" onClick={onToggle}>
          {expanded ? "show fewer" : `show ${levels.withheld} more`}
        </button>
      )}
    </div>
  );
}
