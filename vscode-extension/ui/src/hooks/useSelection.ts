import { useCallback, useEffect, useRef, useState } from "react";
import type { Pick, SelectionItem } from "../api/types";

type Describe = (picks: Pick[]) => Promise<SelectionItem[]>;

/**
 * Selection state for a viewer. `describe` turns clicks into labelled items (the design
 * endpoint stores them; the file endpoint only describes). `initial` re-syncs from the server.
 */
export function useSelection(describe: Describe, text: (items: SelectionItem[]) => string, initial?: SelectionItem[]) {
  const [items, setItems] = useState<SelectionItem[]>(initial ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(items);
  latest.current = items;
  // What the human has clicked so far, ahead of the server's descriptions: a shift-click made while
  // the previous click is still being described must add to it, not to the last described list.
  const picks = useRef<Pick[]>([]);
  const request = useRef(0);
  const pending = useRef(false);

  const initialKey = JSON.stringify((initial ?? []).map((i) => i.id));
  useEffect(() => {
    if (!initial || pending.current) return; // a newer local selection is on its way
    setItems(initial);
    picks.current = initial.map((i) => ({ point: i.point, normal: i.normal, component: i.component }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialKey]);

  const run = useCallback(
    async (next: Pick[]) => {
      picks.current = next;
      const id = ++request.current;
      pending.current = true;
      setBusy(true);
      setError(null);
      try {
        const described = await describe(next);
        if (id === request.current) setItems(described);
      } catch (e) {
        if (id === request.current) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (id === request.current) {
          pending.current = false;
          setBusy(false);
        }
      }
    },
    [describe],
  );

  const select = useCallback((pick: Pick, additive: boolean) => void run(additive ? [...picks.current, pick] : [pick]), [run]);
  const clear = useCallback(() => void run([]), [run]);

  const message = text(items);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && latest.current.length && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) clear();
    };
    // Capture phase: three-cad-viewer binds Escape itself and stops it from bubbling.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [clear]);

  return { items, busy, error, select, clear, text: message };
}
