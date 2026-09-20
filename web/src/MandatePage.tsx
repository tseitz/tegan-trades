import { Fragment, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchReview, type ReviewDocument, type ReviewHeader } from "./api/client";
import { AltSignalSection } from "./altsignal/AltSignalSection";
import { nextSort, sortRows, type GridSort } from "./grid/sort";
import { numericColumns, signedColumns, signTone, verdictTone } from "./grid/tone";
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
    <div className="mb-4 flex flex-col gap-2">
      {header.mismatches.length > 0 && (
        <pre className="panel border-down/50 m-0 font-mono text-xs whitespace-pre-wrap text-down">
          {header.mismatches.join("\n")}
        </pre>
      )}
      {header.stale_banner && (
        <p className="panel border-warn/50 m-0 text-xs text-warn">{header.stale_banner}</p>
      )}
      <p className="m-0 font-mono text-xs text-faint">
        {header.prices}
        {header.written ? ` · ${header.written}` : ""}
        {unpriced > 0 ? ` · excludes ${unpriced} with no price` : ""}
      </p>
    </div>
  );
}

function Title({ children }: { children: string }) {
  return <h1 className="mb-3 font-mono text-lg font-semibold tracking-tight">{children}</h1>;
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
    return (
      <div>
        <Title>{name ?? ""}</Title>
        {error ? (
          <p className="text-down">Failed to load: {error}</p>
        ) : (
          <p className="text-muted">Loading…</p>
        )}
      </div>
    );
  }

  const signed = signedColumns(review.grid.columns);
  const numeric = numericColumns(review.grid.rows);
  const verdictColumn = review.grid.columns.indexOf("");

  if (review.grid.rows.length === 0) {
    return (
      <div>
        <Title>{review.mandate}</Title>
        {error && <p className="mb-3 text-down">Failed to refresh: {error}</p>}
        <ReviewHeaderBlock header={review.header} unpriced={review.grid.totals.unpriced} />
        <p className="text-muted">no positions — nothing to review</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <section>
        <Title>{review.mandate}</Title>
        {/* A transient failed refetch renders above the grid rather than replacing it — a good
            screen must survive a bad poll. */}
        {error && <p className="mb-3 text-down">Failed to refresh: {error}</p>}
        <ReviewHeaderBlock header={review.header} unpriced={review.grid.totals.unpriced} />

        <table className="data-table">
          <thead>
            <tr>
              {review.grid.columns.map((column, i) => {
                const active = sort?.column === i ? sort.direction : null;
                return (
                  <th
                    key={i}
                    className={numeric.has(i) ? "num" : undefined}
                    aria-sort={
                      active === "asc" ? "ascending" : active === "desc" ? "descending" : "none"
                    }
                  >
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
                  {row.cells.map((cell, i) => {
                    const isNumber = cell.value !== null && cell.value !== undefined;
                    const tone =
                      signed.has(i) && isNumber
                        ? signTone(cell.value as number)
                        : i === verdictColumn
                          ? verdictTone(cell.text)
                          : undefined;
                    return (
                      <td key={i} className={[isNumber ? "num" : "", tone ?? ""].join(" ").trim()}>
                        {cell.text}
                      </td>
                    );
                  })}
                </tr>
                {row.notes.map((note) => (
                  <tr key={note.label} className="note-row">
                    <td colSpan={review.grid.columns.length}>
                      <span className={verdictTone(note.label)}>{note.label}</span>
                      {` — ${note.text}`}
                    </td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>

        <pre className="mt-3 mb-0 font-mono text-xs whitespace-pre-wrap text-muted">
          {review.grid.totals.lines.join("\n")}
        </pre>
      </section>

      <LevelsTable
        levels={review.levels}
        expanded={expanded}
        onToggle={() => setExpanded((current) => !current)}
      />
      <AltSignalSection altsignal={review.altsignal} />
    </div>
  );
}
