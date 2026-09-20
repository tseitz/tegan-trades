import { Fragment, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchReview, type ReviewDocument, type ReviewHeader } from "./api/client";
import { AltSignalSection } from "./altsignal/AltSignalSection";
import { nextSort, sortRows, type GridSort } from "./grid/sort";
import { LevelsTable } from "./levels/LevelsTable";
import { useRefresh } from "./refresh/RefreshProvider";

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
  const { completedAt } = useRefresh();
  const [review, setReview] = useState<ReviewDocument | null>(null);
  const [loadedName, setLoadedName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<GridSort | null>(null);
  const [expanded, setExpanded] = useState(false);

  // Its own effect, keyed on [name] alone — the fetch effect below also depends on
  // completedAt, and resetting these on every refresh would throw away the column the reader
  // sorted by.
  useEffect(() => {
    setSort(null);
    setExpanded(false);
  }, [name]);

  useEffect(() => {
    if (!name) return;
    // No setReview(null) here — a refresh-triggered refetch must not blank a grid the reader
    // is looking at. loadedName, not review.mandate !== name, gates the loading state: the
    // wire deliberately does not promise those are equal (wire.py, test_api.py:317-322).
    setError(null);
    fetchReview(name)
      .then((doc) => {
        setReview(doc);
        setLoadedName(name);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [name, completedAt]);

  if (!review || loadedName !== name) {
    if (error) {
      return (
        <div>
          <h1>{name}</h1>
          <p>Failed to load: {error}</p>
        </div>
      );
    }
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
        {error && <p>Failed to refresh: {error}</p>}
        <ReviewHeaderBlock header={review.header} unpriced={review.grid.totals.unpriced} />
        <p>no positions — nothing to review</p>
      </div>
    );
  }

  return (
    <div>
      <h1>{review.mandate}</h1>
      {/* A transient failed refetch renders above the grid rather than replacing it — a good
          screen must survive a bad poll. */}
      {error && <p>Failed to refresh: {error}</p>}
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
      <AltSignalSection altsignal={review.altsignal} />
    </div>
  );
}
