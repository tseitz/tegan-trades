import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { fetchMandates, type MandateList } from "./api/client";
import { useRefresh } from "./refresh/RefreshProvider";

export function Shell() {
  const { state, error, start } = useRefresh();
  const [mandates, setMandates] = useState<MandateList["mandates"]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const running = state === "running";

  useEffect(() => {
    fetchMandates()
      .then((list) => setMandates(list.mandates))
      .catch((err: unknown) => setListError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 flex h-shell-header items-center gap-4 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <NavLink to="/" className="text-xs font-semibold tracking-[0.14em] text-faint uppercase">
          Mandates
        </NavLink>

        <nav className="flex flex-1 items-center gap-1">
          {mandates.map((mandate) => (
            <NavLink
              key={mandate.name}
              to={`/mandate/${mandate.name}`}
              className={({ isActive }) =>
                `rounded-md px-2.5 py-1 font-mono text-xs ${
                  isActive
                    ? "bg-raised text-ink"
                    : "text-muted hover:bg-surface hover:text-ink"
                }`
              }
            >
              {mandate.name}
            </NavLink>
          ))}
          {listError && <span className="text-xs text-down">Mandates failed to load: {listError}</span>}
        </nav>

        {/* Outside `mandates.map` and set off by its own border — Treasury sits beside the
            Mandate switcher, never inside it (`data/treasury.yaml` is not a `data/portfolios/*`
            file, see #94's design note). */}
        <NavLink
          to="/treasury"
          className={({ isActive }) =>
            `rounded-md border-l border-line px-2.5 py-1 pl-3 font-mono text-xs ${
              isActive ? "bg-raised text-ink" : "text-muted hover:bg-surface hover:text-ink"
            }`
          }
        >
          Treasury
        </NavLink>

        {state === "failed" && error && (
          <span className="text-xs text-down">Refresh failed: {error}</span>
        )}
        <button
          type="button"
          onClick={start}
          disabled={running}
          className="rounded-md border border-line bg-surface px-3 py-1 text-xs text-ink hover:bg-raised disabled:cursor-not-allowed disabled:text-faint"
        >
          {running ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      <main className="px-5 py-6">
        <Outlet />
      </main>
    </div>
  );
}
