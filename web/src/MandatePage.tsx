import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchReview, type ReviewDocument } from "./api/client";
import { nextSort, sortRows, type GridSort } from "./grid/sort";

export function MandatePage() {
  const { name } = useParams<{ name: string }>();
  const [review, setReview] = useState<ReviewDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<GridSort | null>(null);

  useEffect(() => {
    if (!name) return;
    setReview(null);
    setError(null);
    setSort(null);
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
        <p>no positions — nothing to review</p>
      </div>
    );
  }

  return (
    <div>
      <h1>{review.mandate}</h1>
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
            <tr key={row.ticker}>
              {row.cells.map((cell, i) => (
                <td key={i}>{cell.text}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <pre>{review.grid.totals.lines.join("\n")}</pre>
    </div>
  );
}
