import { Fragment, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchReview, type ReviewDocument, type ReviewHeader } from "./api/client";
import { nextSort, sortRows, type GridSort } from "./grid/sort";
import { LevelsTable } from "./levels/LevelsTable";

interface ReviewHeaderBlockProps {
  header: ReviewHeader;
  unpriced: number;
}

// Riskiest first, the same order `render()` prints them and for the same reason
// (`render.py:132-137`): above the table, never below, because by then every verdict has
// been read as fact.
function ReviewHeaderBlock({ header, unpriced }: ReviewHeaderBlockProps) {
  return (
    <>
      {header.mismatches.length > 0 && <pre>{header.mismatches.join("\n")}</pre>}
      {header.stale_banner && <p>{header.stale_banner}</p>}
      <p>
        {header.prices}
        {header.written ? ` · ${header.written}` : ""}
        {unpriced > 0 ? ` · excludes ${unpriced} with no price` : ""}
      </p>
    </>
  );
}

export function MandatePage() {
  const { name } = useParams<{ name: string }>();
  const [review, setReview] = useState<ReviewDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<GridSort | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!name) return;
    setReview(null);
    setError(null);
    setSort(null);
    setExpanded(false);
    fetchReview(name)
      .then(setReview)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [name]);

  if (error) {
    return (
      <div>
        <h1>{name}</h1>
        <p>Failed to load: {error}</p>
      </div>
    );
  }

  if (!review) {
    return (
      <div>
        <h1>{name}</h1>
        <p>Loading…</p>
      </div>
    );
  }

  if (review.grid.rows.length === 0) {
    return (
      <div>
        <h1>{review.mandate}</h1>
        <ReviewHeaderBlock header={review.header} unpriced={review.grid.totals.unpriced} />
        <p>no positions — nothing to review</p>
      </div>
    );
  }

  return (
    <div>
      <h1>{review.mandate}</h1>
      <ReviewHeaderBlock header={review.header} unpriced={review.grid.totals.unpriced} />
      <table>
        <thead>
          <tr>
            {review.grid.columns.map((column, i) => {
              const active = sort?.column === i ? sort.direction : null;
              return (
                <th key={i} aria-sort={active === "asc" ? "ascending" : active === "desc" ? "descending" : "none"}>
                  <button
                    type="button"
                    onClick={() => setSort(nextSort(review.grid.rows, sort, i))}
                    aria-label={column === "" ? "verdict" : undefined}
                  >
                    {column}
                    {active === "asc" ? " ▲" : active === "desc" ? " ▼" : null}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortRows(review.grid.rows, sort).map((row) => (
            <Fragment key={row.ticker}>
              <tr className={row.unpriced ? "unpriced" : undefined}>
                {row.cells.map((cell, i) => (
                  <td key={i}>{cell.text}</td>
                ))}
              </tr>
              {row.notes.map((note) => (
                <tr key={note.label}>
                  <td colSpan={review.grid.columns.length}>
                    {note.label} — {note.text}
                  </td>
                </tr>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
      <pre>{review.grid.totals.lines.join("\n")}</pre>
      <LevelsTable
        levels={review.levels}
        expanded={expanded}
        onToggle={() => setExpanded((current) => !current)}
      />
    </div>
  );
}
