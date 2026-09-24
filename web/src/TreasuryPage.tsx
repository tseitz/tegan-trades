import { useEffect, useState } from "react";
import { fetchTreasury, type TreasuryResponse } from "./api/client";
import { useDocumentTitle } from "./documentTitle";
import { useRefresh } from "./refresh/RefreshProvider";

const MONEY = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export function TreasuryPage() {
  useDocumentTitle("Treasury");
  const { completedAt } = useRefresh();
  const [data, setData] = useState<TreasuryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // No setData(null) here — a refresh-triggered refetch must not blank a card the reader is
    // looking at, the same reason `MandatePage`'s own fetch effect skips it.
    setError(null);
    fetchTreasury()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [completedAt]);

  if (!data) {
    return (
      <div>
        <Title>Treasury</Title>
        {error ? (
          <p className="text-down">Failed to load: {error}</p>
        ) : (
          <p className="text-muted">Loading…</p>
        )}
      </div>
    );
  }

  const { treasury, empty_note: emptyNote } = data;

  if (!treasury) {
    return (
      <div>
        <Title>Treasury</Title>
        {error && <p className="mb-3 text-down">Failed to refresh: {error}</p>}
        <p className="text-muted">{emptyNote}</p>
      </div>
    );
  }

  const showIdle = treasury.idle.length > 0 || treasury.advice.length > 0;

  return (
    <div className="flex flex-col gap-8">
      <section>
        <Title>{treasury.header.mandate}</Title>
        {error && <p className="mb-3 text-down">Failed to refresh: {error}</p>}
        <p className="m-0 mb-3 font-mono text-xs text-faint">
          {treasury.header.rows} row(s) · {MONEY.format(treasury.header.total)} total · as of{" "}
          {treasury.header.as_of}
          {treasury.header.written ? ` · ${treasury.header.written}` : ""}
        </p>

        <table className="data-table">
          <thead>
            <tr>
              <th>WHAT</th>
              <th className="num">AMOUNT</th>
              <th>VENUE</th>
              <th className="num">APY</th>
              <th aria-label="since and safety" />
            </tr>
          </thead>
          <tbody>
            {treasury.rows.map((row, i) => (
              <tr key={i}>
                <td>{row.what}</td>
                <td className="num">{MONEY.format(row.amount)}</td>
                <td>{row.venue}</td>
                <td className="num">{row.apy}</td>
                <td className="text-muted">
                  {row.since}
                  {row.safety ? ` · ${row.safety}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {treasury.apy_line && (
          <p className="mt-3 mb-0 font-mono text-xs text-muted">{treasury.apy_line}</p>
        )}
      </section>

      <section>
        <h2 className="mb-3 font-mono text-sm font-semibold text-ink">BENCHMARK</h2>
        {treasury.benchmark_note ? (
          <p className="text-muted">{treasury.benchmark_note}</p>
        ) : (
          <div className="flex flex-wrap gap-4 font-mono text-xs text-muted">
            {treasury.benchmark_cells.map((cell) => (
              <span key={cell.label}>
                {cell.label} <span className="text-ink">{cell.text}</span>
              </span>
            ))}
          </div>
        )}
        {treasury.readings_line && (
          <p className="mt-2 mb-0 font-mono text-xs text-faint">{treasury.readings_line}</p>
        )}
      </section>

      {showIdle && (
        <section className="flex flex-col gap-6">
          <div>
            <h2 className="mb-3 font-mono text-sm font-semibold text-ink">{treasury.idle_title}</h2>
            <table className="data-table">
              <thead>
                <tr>
                  <th>ACCOUNT</th>
                  <th className="num">AMOUNT</th>
                </tr>
              </thead>
              <tbody>
                {treasury.idle.map((row, i) => (
                  <tr key={i}>
                    <td>
                      {row.mandate}/{row.account}
                    </td>
                    <td className="num">{MONEY.format(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <h2 className="mb-3 font-mono text-sm font-semibold text-ink">{treasury.advice_title}</h2>
            <table className="data-table">
              <thead>
                <tr>
                  <th>VENUE</th>
                  <th className="num">APY</th>
                </tr>
              </thead>
              <tbody>
                {treasury.advice.map((row) => (
                  <tr key={row.slug}>
                    <td>{row.slug}</td>
                    <td className="num">{row.apy}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function Title({ children }: { children: string }) {
  return <h1 className="mb-3 font-mono text-lg font-semibold tracking-tight">{children}</h1>;
}
