import { useEffect, useState } from "react";
import { json } from "../api/client";
import { loadScene, type CadScene } from "../lib/scene";
import type { PrintShading } from "../lib/shading";

/**
 * Load a 3D view's scene and its part views from the server; errors carry the server's reason (e.g. 415/422 detail).
 * A new `version` of the same URL (the files of another build of the revision) loads again, the model on screen
 * staying until it has.
 */
export function useScene(url: string | null, version?: string): { data: CadScene | null; error: string | null } {
  const [state, setState] = useState<{ url: string | null; data: CadScene | null; error: string | null }>({ url: null, data: null, error: null });

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

/** The print view's shading (`print_shading`), once read; none while it loads, or where the revision has none. */
export function useShading(url: string | null): PrintShading | null {
  const [state, setState] = useState<{ url: string; shading: PrintShading } | null>(null);
  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    fetch(url).then((res) => json<PrintShading>(res)).then((shading) => !cancelled && setState({ url, shading }))
      .catch(() => undefined); // the view stands without its shading
    return () => {
      cancelled = true;
    };
  }, [url]);
  return state?.url === url ? state.shading : null;
}
