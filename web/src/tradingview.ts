// Where a ticker goes when you click it — same URL the digest email links to
// (packages/digest/src/digest/htmlmail.py CHART). TradingView resolves a bare symbol across
// exchanges, so no venue prefix is needed and none would be right for a list mixing equities,
// coins, and funds.
export function tradingViewUrl(ticker: string): string {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(ticker)}`;
}
