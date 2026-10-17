import { Fragment, useEffect, useState } from "react";
import { fetchExposure, type ExposureResponse } from "./api/client";
import { useDocumentTitle } from "./documentTitle";
import { MONEY } from "./NetworthPage";
import { useRefresh } from "./refresh/RefreshProvider";

const PCT = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const pct = (value: number | null) => (value === null ? "—" : PCT.format(value));

export function ExposurePage() {
  useDocumentTitle("Exposure");
  const { completedAt } = useRefresh();
  const [data, setData] = useState<ExposureResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    setError(null);
    fetchExposure()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [completedAt]);

  if (!data) {
    return (
      <div>
        <Title>Exposure</Title>
        {error ? (
          <p className="text-down">Failed to load: {error}</p>
        ) : (
          <p className="text-muted">Loading…</p>
        )}
      </div>
    );
  }

  const toggle = (asset: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(asset)) next.add(asset);
      return next;
    });

  return (
    <div className="flex flex-col gap-4">
      <section>
        <Title>Exposure</Title>
        {error && <p className="mb-3 text-down">Failed to refresh: {error}</p>}
        {data.total !== null && (
          <p className="m-0 mb-1">
            <span className="font-mono text-3xl font-semibold tabular-nums">
              {MONEY.format(data.total)}
            </span>{" "}
            <span className="text-xs text-muted">live</span>
          </p>
        )}
        <p className="m-0 mb-4 font-mono text-xs text-faint">{data.prices}</p>
        {data.problems.length > 0 && (
          <ul className="m-0 mb-4 list-none p-0 text-down">
            {data.problems.map((problem, i) => (
              <li key={i}>{problem}</li>
            ))}
          </ul>
        )}

        {data.total === null ? (
          <p className="text-muted">Nothing to sum.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>ASSET</th>
                <th className="num">VALUE</th>
                <th className="num">% OF NET WORTH</th>
                <th aria-label="share of net worth" />
              </tr>
            </thead>
            <tbody>
              {data.slices.map((slice) => {
                const expanded = open.has(slice.asset);
                return (
                  <Fragment key={slice.asset}>
                    <tr>
                      <td>
                        <button
                          type="button"
                          aria-expanded={expanded}
                          onClick={() => toggle(slice.asset)}
                          className="cursor-pointer border-0 bg-transparent p-0 font-mono text-ink"
                        >
                          <span className="inline-block w-4 text-faint">{expanded ? "▾" : "▸"}</span>
                          {slice.asset}
                        </button>
                      </td>
                      <td className="num">{MONEY.format(slice.value)}</td>
                      <td className="num">{pct(slice.pct)}</td>
                      <td className="w-1/3">
                        <div
                          className="h-1.5 rounded-sm bg-accent"
                          style={{ width: `${Math.max(0, Math.min(1, slice.pct ?? 0)) * 100}%` }}
                        />
                      </td>
                    </tr>
                    {expanded &&
                      slice.parts.map((part, i) => (
                        <tr key={i} className="text-muted">
                          <td className="pl-8">
                            {part.book} · {part.ticker}
                          </td>
                          <td className="num">{MONEY.format(part.value)}</td>
                          <td className="num">
                            {slice.value === 0 ? "—" : PCT.format(part.value / slice.value)}
                          </td>
                          <td />
                        </tr>
                      ))}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}

        {data.excluded.length > 0 && (
          <p className="mt-3 mb-0 font-mono text-xs text-faint">
            excluded:{" "}
            {data.excluded.map((e) => `${e.book}/${e.ticker} (${e.reason})`).join(", ")}
          </p>
        )}
      </section>
    </div>
  );
}

function Title({ children }: { children: string }) {
  return <h1 className="mb-3 font-mono text-lg font-semibold tracking-tight">{children}</h1>;
}
