import { Fragment } from "react";
import type { AltSignalSection as AltSignalSectionData } from "../api/client";

interface AltSignalSectionProps {
  altsignal: AltSignalSectionData;
}

export function AltSignalSection({ altsignal }: AltSignalSectionProps) {
  return (
    <div>
      <h2>{altsignal.title}</h2>
      {altsignal.empty_note ? (
        <p>{altsignal.empty_note}</p>
      ) : (
        <>
          {altsignal.chains.map((chain) => (
            <Fragment key={chain.ticker}>
              <p>{chain.ticker}</p>
              {chain.lines.map((line, i) => (
                <p key={i}>{line}</p>
              ))}
            </Fragment>
          ))}
          {altsignal.macro.length > 0 && (
            <>
              <p>{altsignal.macro_label}</p>
              {altsignal.macro.map((line, i) => (
                <p key={i}>{line}</p>
              ))}
            </>
          )}
        </>
      )}
    </div>
  );
}
