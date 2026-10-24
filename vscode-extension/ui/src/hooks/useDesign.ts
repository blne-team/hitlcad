import { useCallback, useEffect, useRef, useState } from "react";
import { api, subscribe } from "../api/client";
import type { DesignState } from "../api/types";

interface LiveDesign {
  state: DesignState | null;
  /** null until the event stream first opens. */
  connected: boolean | null;
  error: string | null;
  refresh: () => void;
}

/**
 * Live design: the event stream signals changes and the authoritative state is refetched
 * (coalesced), so the client never re-implements the server's projection.
 */
export function useDesign(id: string): LiveDesign {
  const [state, setState] = useState<DesignState | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const fetching = useRef(false);
  const again = useRef(false);

  const refresh = useCallback(() => {
    if (fetching.current) {
      again.current = true; // fetched once more when the fetch under way ends
      return;
    }
    fetching.current = true;
    void (async () => {
      do {
        again.current = false;
        try {
          setState(await api.design(id));
          setError(null);
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } while (again.current);
      fetching.current = false;
    })();
  }, [id]);

  useEffect(() => {
    refresh();
    const unsubscribe = subscribe(
      id,
      () => {
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(refresh, 60);
      },
      setConnected,
    );
    return () => {
      unsubscribe();
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [id, refresh]);

  return { state, connected, error, refresh };
}
