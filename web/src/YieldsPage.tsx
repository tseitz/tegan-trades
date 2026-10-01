import { useEffect, useState } from "react";
import { fetchYields, type YieldsResponse } from "./api/client";
import { useDocumentTitle } from "./documentTitle";
import { useRefresh } from "./refresh/RefreshProvider";

export function YieldsPage() {
  useDocumentTitle("Yields");
  const { completedAt } = useRefresh();
  const [data, setData] = useState<YieldsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // No setData(null) here — a refresh-triggered refetch must not blank a card the reader is
    // looking at, the same reason `TreasuryPage`'s own fetch effect skips it.
    setError(null);
    fetchYields()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [completedAt]);

  if (!data) {
    return (
      <div>
        <Title>Yields</Title>
        {error ? (
          <p className="text-down">Failed to load: {error}</p>
        ) : (
          <p className="text-muted">Loading…</p>
        )}
      </div>
    );
  }

  const { yields: card } = data;

  return (
    <div className="flex flex-col gap-8">
      <section>
        <Title>Yields</Title>
        {error && <p className="mb-3 text-down">Failed to refresh: {error}</p>}
        <p className="m-0 mb-6 font-mono text-xs text-faint">{card.summary}</p>

        {card.assets.length === 0 ? (
          <p className="text-muted">Nothing to rank yet.</p>
        ) : (
          <div className="flex flex-col gap-6">
            {card.assets.map((asset) => (
              <div key={asset.asset}>
                <h2 className="mb-1 font-mono text-sm font-semibold text-ink">
                  {asset.asset}{" "}
                  <span className="font-normal text-muted">
                    · held by {asset.mandates.join(", ")}
                  </span>
                </h2>
                {asset.held_state_note && (
                  <p className="m-0 mb-2 text-xs text-muted">{asset.held_state_note}</p>
                )}
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>WRAPPER</th>
                      <th className="num">APY</th>
                      <th>POOL</th>
                      <th aria-label="held and safety" />
                    </tr>
                  </thead>
                  <tbody>
                    {asset.options.map((option, i) => (
                      <tr key={i}>
                        <td>{option.wrapper}</td>
                        <td className="num">{option.apy}</td>
                        <td className="text-faint">{option.pool_id}</td>
                        <td className="text-muted">
                          {option.safety}
                          {option.held !== null ? ` · HELD (${option.held})` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}

        {card.readings_line && (
          <p className="mt-3 mb-0 font-mono text-xs text-faint">{card.readings_line}</p>
        )}
      </section>
    </div>
  );
}

function Title({ children }: { children: string }) {
  return <h1 className="mb-3 font-mono text-lg font-semibold tracking-tight">{children}</h1>;
}
