import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { fetchNetworth, type NetworthResponse, type NetworthRow } from "./api/client";
import { useDocumentTitle } from "./documentTitle";
import { useRefresh } from "./refresh/RefreshProvider";

const MONEY = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const COMPACT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});
const PCT = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
  signDisplay: "always",
});

// `days` matches a server horizon where one exists, so the headline agrees with the email.
const RANGES = [
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "3M", days: 91 },
  { label: "1Y", days: 365 },
  { label: "All", days: null },
] as const;

type Range = (typeof RANGES)[number];

const H = 260;
const PAD = { top: 12, right: 12, bottom: 24, left: 64 };

export function NetworthPage() {
  useDocumentTitle("Net worth");
  const { completedAt } = useRefresh();
  const [data, setData] = useState<NetworthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<Range>(RANGES[1]);

  useEffect(() => {
    let current = true;
    setError(null);
    fetchNetworth()
      .then((next) => current && setData(next))
      .catch((err: unknown) => current && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      current = false;
    };
  }, [completedAt]);

  const rows = useMemo(() => inRange(data?.rows ?? [], range), [data, range]);

  if (!data) {
    return (
      <div>
        <Title>Net worth</Title>
        {error ? <p className="text-down">Failed to load: {error}</p> : <p className="text-muted">Loading…</p>}
      </div>
    );
  }

  const latest = data.rows.at(-1);
  if (!latest) {
    return (
      <div>
        <Title>Net worth</Title>
        <p className="text-muted">No history yet — the nightly records one row per night.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <section>
        <Title>Net worth</Title>
        {error && <p className="mb-3 text-down">Failed to refresh: {error}</p>}
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="font-mono text-3xl font-semibold tabular-nums">{MONEY.format(latest.total)}</span>
          <Change data={data} rows={rows} range={range} />
        </div>
        <p className="m-0 mt-1 font-mono text-xs text-faint">
          as of {latest.date} · {Object.keys(latest.by_book).length} book(s) + treasury · excludes venue
          positions
          {latest.unpriced ? ` · excludes ${latest.unpriced} unpriced` : ""}
        </p>
      </section>

      <div className="flex gap-1" role="group" aria-label="Range">
        {RANGES.map((r) => (
          <button
            key={r.label}
            type="button"
            onClick={() => setRange(r)}
            aria-pressed={r === range}
            className={`rounded-md px-2.5 py-1 font-mono text-xs ${
              r === range ? "bg-raised text-ink" : "text-muted hover:bg-surface hover:text-ink"
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>

      {rows.length < 2 ? (
        <p className="text-muted">
          {rows.length === 1 ? "One night" : "No nights"} recorded in this range — the line starts once
          there are two.
        </p>
      ) : (
        <Chart rows={rows} />
      )}

      <details>
        <summary className="cursor-pointer font-mono text-xs text-muted">Rows</summary>
        <table className="data-table mt-2">
          <thead>
            <tr>
              <th>DATE</th>
              <th className="num">TOTAL</th>
              <th className="num">TREASURY</th>
              <th>NOTE</th>
            </tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((row) => (
              <tr key={row.date}>
                <td>{row.date}</td>
                <td className="num">{MONEY.format(row.total)}</td>
                <td className="num">{MONEY.format(row.parked)}</td>
                <td className="text-muted">{caveat(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

function inRange(rows: NetworthRow[], range: Range): NetworthRow[] {
  const last = rows.at(-1);
  if (!last || range.days === null) return rows;
  const start = new Date(last.date);
  start.setUTCDate(start.getUTCDate() - range.days);
  const from = start.toISOString().slice(0, 10);
  return rows.filter((r) => r.date >= from);
}

function caveat(row: NetworthRow): string {
  const parts = [];
  if (row.stale) parts.push("prices stale");
  if (row.mismatched.length) parts.push(`left out ${row.mismatched.join(", ")}`);
  return parts.join(" · ");
}

function partial(row: NetworthRow): boolean {
  return row.stale || row.mismatched.length > 0;
}

function Change({ data, rows, range }: { data: NetworthResponse; rows: NetworthRow[]; range: Range }) {
  const horizon = data.horizons.find((h) => h.days === range.days);
  if (horizon) {
    return <Delta amount={horizon.amount} pct={horizon.pct} since={horizon.baseline_date} uneven={horizon.uneven} />;
  }
  const first = rows.find((r) => !partial(r));
  const last = rows.at(-1);
  if (!first || !last || first === last) return null;
  return (
    <Delta
      amount={last.total - first.total}
      pct={first.total ? (last.total - first.total) / first.total : null}
      since={first.date}
      uneven={first.unpriced !== last.unpriced}
    />
  );
}

function Delta({ amount, pct, since, uneven }: { amount: number; pct: number | null; since: string; uneven: boolean }) {
  const tone = amount > 0 ? "text-up" : amount < 0 ? "text-down" : "text-muted";
  return (
    <span className="font-mono text-sm tabular-nums">
      <span className={tone}>
        {amount >= 0 ? "+" : ""}
        {MONEY.format(amount)}
        {pct !== null ? ` (${PCT.format(pct)})` : ""}
      </span>
      <span className="text-faint">
        {" "}
        since {since}
        {uneven ? " · coverage differs" : ""}
      </span>
    </span>
  );
}

function Chart({ rows }: { rows: NetworthRow[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  // The viewBox tracks the real width so text and stroke sizes stay in CSS pixels.
  const [W, setW] = useState(800);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setW(Math.max(320, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const totals = rows.map((r) => r.total);
  const lo = Math.min(...totals);
  const hi = Math.max(...totals);
  const span = hi - lo || Math.max(hi * 0.01, 1);
  const yMin = lo - span * 0.08;
  const yMax = hi + span * 0.08;
  const x = (i: number) => PAD.left + (i / (rows.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);
  const ticks = [0, 1, 2, 3].map((k) => yMin + ((yMax - yMin) * k) / 3);
  const path = rows.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r.total).toFixed(1)}`).join("");

  function onMove(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - PAD.left) / (W - PAD.left - PAD.right)) * (rows.length - 1));
    setHover(Math.max(0, Math.min(rows.length - 1, i)));
  }

  const at = hover === null ? null : rows[hover];

  return (
    <div ref={box} className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" role="img" aria-label="Net worth over time">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--color-hairline)" />
            <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-faint font-mono text-[11px]">
              {COMPACT.format(t)}
            </text>
          </g>
        ))}
        <text x={PAD.left} y={H - 6} className="fill-faint font-mono text-[11px]">
          {rows[0].date}
        </text>
        <text x={W - PAD.right} y={H - 6} textAnchor="end" className="fill-faint font-mono text-[11px]">
          {rows.at(-1)!.date}
        </text>
        <path d={path} fill="none" stroke="var(--color-accent)" strokeWidth={2} strokeLinejoin="round" />
        {rows.map((r, i) =>
          partial(r) ? (
            <circle key={r.date} cx={x(i)} cy={y(r.total)} r={4} fill="var(--color-warn)" stroke="var(--color-canvas)" strokeWidth={2} />
          ) : null,
        )}
        {at && hover !== null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--color-line)" />
            <circle cx={x(hover)} cy={y(at.total)} r={4} fill="var(--color-accent)" stroke="var(--color-canvas)" strokeWidth={2} />
          </g>
        )}
        <rect
          x={PAD.left}
          y={0}
          width={W - PAD.left - PAD.right}
          height={H}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      {at && hover !== null && (
        <div
          className="pointer-events-none absolute top-0 rounded-md border border-line bg-raised px-2 py-1 font-mono text-xs"
          style={{
            left: `${(x(hover) / W) * 100}%`,
            transform: hover > rows.length / 2 ? "translateX(calc(-100% - 8px))" : "translateX(8px)",
          }}
        >
          <div className="text-faint">{at.date}</div>
          <div className="tabular-nums">{MONEY.format(at.total)}</div>
          {caveat(at) && <div className="text-warn">{caveat(at)}</div>}
        </div>
      )}
    </div>
  );
}

function Title({ children }: { children: string }) {
  return <h1 className="mb-3 font-mono text-lg font-semibold tracking-tight">{children}</h1>;
}
