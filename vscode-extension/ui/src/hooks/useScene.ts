import { useEffect, useState } from "react";
import type { Shapes } from "three-cad-viewer";
import { loadScene } from "../lib/scene";

/**
 * Load a 3D view's scene and its part views from the server; errors carry the server's reason (e.g. 415/422 detail).
 * A new `version` of the same URL (the files of another build of the revision) loads again, the model on screen
 * staying until it has.
 */
export function useScene(url: string | null, version?: string): { data: Shapes | null; error: string | null } {
  const [state, setState] = useState<{ url: string | null; data: Shapes | null; error: string | null }>({ url: null, data: null, error: null });

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    loadScene(url)
      .then((data) => !cancelled && setState({ url, data, error: null }))
      .catch((e: unknown) => !cancelled && setState({ url, data: null, error: e instanceof Error ? e.message : String(e) }));
    return () => {
      cancelled = true;
    };
  }, [url, version]);

  // Never hand out the previous URL's data while the new one loads.
  return state.url === url ? { data: state.data, error: state.error } : { data: null, error: null };
}
