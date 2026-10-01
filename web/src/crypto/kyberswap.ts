// Chain a ticker trades on, for building a KyberSwap deep link. Not a general venue map —
// just enough to route TRIM/ADD off the review grid. Unlisted tickers default to Ethereum,
// where every wallet-synced position lives today (packages/oracle/src/oracle/wallet.py
// DEFAULT_EVM_NETWORKS).
const CHAIN_BY_TICKER: Record<string, string> = {
  AERO: "base",
};

const DEFAULT_CHAIN = "ethereum";
const DEFAULT_QUOTE = "usdc";

export function kyberSwapUrl(ticker: string, verdict: "TRIM" | "ADD"): string {
  const chain = CHAIN_BY_TICKER[ticker.toUpperCase()] ?? DEFAULT_CHAIN;
  const symbol = ticker.toLowerCase();
  const [from, to] = verdict === "TRIM" ? [symbol, DEFAULT_QUOTE] : [DEFAULT_QUOTE, symbol];
  return `https://kyberswap.com/swap/${chain}/${from}-to-${to}`;
}
