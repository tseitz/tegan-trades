// Colour reserved for semantics: which way a verdict points, and which way a number does.
// Nothing here changes a cell's text — `render.py` still owns every word on the screen.

// Sell-side down, buy-side up, WATCH amber. Deliberately partial: `core/review.py` owns this
// vocabulary, and a verdict added there must arrive here uncoloured rather than mis-coloured,
// so an unknown one falls through to neutral instead of guessing.
const VERDICT_TONE: Record<string, string> = {
  TRIM: "text-down",
  SELL_ZONE: "text-down",
  ADD: "text-up",
  BUY_ZONE: "text-up",
  WATCH: "text-warn",
};

export function verdictTone(verdict: string): string {
  return VERDICT_TONE[verdict] ?? "text-muted";
}

// Only the columns where a minus sign means a loss. A short position's SHARES and VALUE are
// also negative, and painting those red would read as losing money on a winning trade.
const SIGNED_HEADERS = new Set(["P&L", "P&L %"]);

export function signedColumns(columns: string[]): Set<number> {
  const signed = new Set<number>();
  columns.forEach((column, i) => {
    if (SIGNED_HEADERS.has(column)) signed.add(i);
  });
  return signed;
}

// From the rows, not the headers: `ReviewCell.value` is non-null exactly for the numeric
// columns, so the header cannot drift out of step with the cells under it.
export function numericColumns(rows: { cells: { value?: number | null }[] }[]): Set<number> {
  const numeric = new Set<number>();
  for (const row of rows) {
    row.cells.forEach((cell, i) => {
      if (cell.value !== null && cell.value !== undefined) numeric.add(i);
    });
  }
  return numeric;
}

export function signTone(value: number): string {
  if (value > 0) return "text-up";
  if (value < 0) return "text-down";
  return "text-muted";
}
