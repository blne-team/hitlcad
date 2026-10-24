import { useEffect, useState } from "react";
import { ApiError } from "../api/client";

/** Fetch a 3D view (JSON) from the server; errors carry the server's reason (e.g. 415/422 detail). */
export function useViewJson(url: string | null): { data: unknown; error: string | null } {
  const [state, setState] = useState<{ url: string | null; data: unknown; error: string | null }>({ url: null, data: null, error: null });

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    fetch(url)
      .then(async (res) => {
        if (!res.ok) {
          let detail = res.statusText;
          try {
            detail = ((await res.json()) as { detail?: string }).detail ?? detail;
          } catch {
            // keep statusText
          }
          throw new ApiError(res.status, detail);
        }
        return res.json() as Promise<unknown>;
      })
      .then((data) => !cancelled && setState({ url, data, error: null }))
      .catch((e: unknown) => !cancelled && setState({ url, data: null, error: e instanceof Error ? e.message : String(e) }));
    return () => {
      cancelled = true;
    };
  }, [url]);

  // Never hand out the previous URL's data while the new one loads.
  return state.url === url ? { data: state.data, error: state.error } : { data: null, error: null };
}
