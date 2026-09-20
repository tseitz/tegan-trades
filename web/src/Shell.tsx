import { Outlet } from "react-router-dom";
import { useRefresh } from "./refresh/RefreshProvider";

// Only the Refresh control moves in here. The Mandate switcher stays in App — hoisting it is a
// separate change.
export function Shell() {
  const { state, error, start } = useRefresh();
  const running = state === "running";

  return (
    <div>
      <header>
        <button type="button" onClick={start} disabled={running}>
          {running ? "Refreshing…" : "Refresh"}
        </button>
        {state === "failed" && error && <span> Refresh failed: {error}</span>}
      </header>
      <Outlet />
    </div>
  );
}
