import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchRefreshStatus, startRefresh, type RefreshJobStatus } from "../api/client";

type RefreshState = "idle" | "running" | "succeeded" | "failed";

interface RefreshContextValue {
  state: RefreshState;
  error: string | null;
  start: () => void;
  completedAt: number | null;
}

const RefreshContext = createContext<RefreshContextValue | null>(null);

const POLL_INTERVAL_MS = 2000;

export function RefreshProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<RefreshState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [completedAt, setCompletedAt] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Closes the window between a click and the interval being set up — without it, a second
  // click before the POST resolves starts a second poller for the same job, and only one of
  // the two ever gets cleared.
  const startInFlightRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // A terminal `failed` sets `error` but does not bump `completedAt` — a failed refresh must
  // not look like a fresh screen to MandatePage, which refetches only when completedAt moves.
  const applyStatus = useCallback(
    (status: RefreshJobStatus) => {
      if (status.state === "running") return;
      stopPolling();
      if (status.state === "succeeded") {
        setState("succeeded");
        setCompletedAt(Date.now());
        setError(null);
        return;
      }
      const failing = status.steps.find((step) => step.state === "failed");
      setState("failed");
      setError(failing ? `${failing.name}: ${failing.detail ?? "failed"}` : "refresh failed");
    },
    [stopPolling],
  );

  const poll = useCallback(
    (jobId: string) => {
      fetchRefreshStatus(jobId)
        .then(applyStatus)
        .catch((err: unknown) => {
          stopPolling();
          setState("failed");
          setError(err instanceof Error ? err.message : String(err));
        });
    },
    [applyStatus, stopPolling],
  );

  const start = useCallback(() => {
    if (startInFlightRef.current || timerRef.current !== null) return;
    startInFlightRef.current = true;
    setState("running");
    setError(null);
    startRefresh()
      .then((status) => {
        applyStatus(status);
        if (status.state === "running") {
          timerRef.current = setInterval(() => poll(status.id), POLL_INTERVAL_MS);
        }
      })
      .catch((err: unknown) => {
        setState("failed");
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        startInFlightRef.current = false;
      });
  }, [applyStatus, poll]);

  // main.tsx renders StrictMode on React 19, so effects double-invoke in dev — the cleanup
  // here is what keeps that from leaving two pollers running.
  useEffect(() => stopPolling, [stopPolling]);

  return (
    <RefreshContext.Provider value={{ state, error, start, completedAt }}>
      {children}
    </RefreshContext.Provider>
  );
}

export function useRefresh(): RefreshContextValue {
  const value = useContext(RefreshContext);
  if (!value) {
    throw new Error("useRefresh must be used within a RefreshProvider");
  }
  return value;
}
