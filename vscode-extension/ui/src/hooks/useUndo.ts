import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { DesignState, PanelEdit } from "../api/types";

/** Ctrl or ⌘, as the shortcuts read on this machine. */
export const MOD = /Mac/.test(navigator.platform) ? "⌘" : "Ctrl+";

/** Where a key types text: those keys undo the typing there, not the design. */
function typing(target: EventTarget | null): boolean {
  if (target instanceof HTMLInputElement) return !["checkbox", "radio", "range", "button"].includes(target.type);
  return target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || (target instanceof HTMLElement && target.isContentEditable);
}

/**
 * Undo and Redo of the human's panel edits (`DesignState.undo` and `redo`), from the buttons and from Ctrl+Z,
 * Ctrl+Shift+Z and Ctrl+Y (⌘ on macOS) outside text fields. Each takes back the step it names, merged with what
 * others changed since, and rebuilds. A step is sent once: `undo` and `redo` are null while the one sent is still
 * the latest the design's state lists, and when there is none.
 */
export function useUndo(id: string, state: DesignState | null) {
  const [sent, setSent] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ready = (steps: PanelEdit[] | undefined) => {
    const step = steps?.[steps.length - 1];
    return step && step.version !== sent ? step : null;
  };
  const undo = ready(state?.undo);
  const redo = ready(state?.redo);

  const revert = useCallback((step: PanelEdit | null) => {
    if (!step) return;
    setSent(step.version);
    setError(null);
    api.revert(id, step.version).catch((e: unknown) => {
      setSent(null);
      setError(e instanceof Error ? e.message : String(e));
    });
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || typing(e.target)) return;
      const key = e.key.toLowerCase();
      if (key !== "z" && !(key === "y" && !e.shiftKey)) return;
      e.preventDefault();
      revert(key === "z" && !e.shiftKey ? undo : redo);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo, revert]);

  return { undo, redo, error, revert };
}
