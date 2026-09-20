import type { AltSignalSection as AltSignalSectionData } from "../api/client";

interface AltSignalSectionProps {
  altsignal: AltSignalSectionData;
}

export function AltSignalSection({ altsignal }: AltSignalSectionProps) {
  return (
    <section>
      <h2 className="mb-3 font-mono text-sm font-semibold text-ink">{altsignal.title}</h2>
      {altsignal.empty_note ? (
        <p className="text-muted">{altsignal.empty_note}</p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(20rem,1fr))] gap-3">
            {altsignal.chains.map((chain) => (
              <div key={chain.ticker} className="panel">
                <p className="m-0 mb-1.5 font-mono text-xs font-semibold text-ink">
                  {chain.ticker}
                </p>
                {chain.lines.map((line, i) => (
                  <p key={i} className="m-0 font-mono text-xs text-muted">
                    {line}
                  </p>
                ))}
              </div>
            ))}
          </div>
          {/* Full width, not another card in the grid above: a macro row carries a sentence of
              prose per market, and a chain card's column width wraps it into a ribbon. */}
          {altsignal.macro.length > 0 && (
            <div className="panel">
              <p className="m-0 mb-1.5 font-mono text-xs font-semibold text-ink">
                {altsignal.macro_label}
              </p>
              {altsignal.macro.map((line, i) => (
                <p key={i} className="m-0 font-mono text-xs text-muted">
                  {line}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
