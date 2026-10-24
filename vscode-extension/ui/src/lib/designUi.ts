import { useCallback, useEffect, useState } from "react";
import type { Revision } from "../api/types";
import { toExtension } from "./host";

export type InspectorTab = "pins" | "parameters" | "changes" | "report" | "brief";

/**
 * UI state a design's 3D editor and its Design view share. They are separate webviews: each sends
 * patches to the extension, which merges them and sends the state back to both.
 */
export interface DesignUi {
  /** Revision on screen; null follows the latest successful build. */
  revision: number | null;
  /** Revision compared against; null means the one before `revision`. */
  base: number | null;
  compare: boolean;
  placing: boolean;
  activePin: string | null;
  tab: InspectorTab;
}

const INITIAL: DesignUi = { revision: null, base: null, compare: false, placing: false, activePin: null, tab: "pins" };

type Update = (patch: Partial<DesignUi>, reveal?: boolean) => void;

/**
 * The shared UI state of the design this page shows, kept by the extension. `reveal` asks VS Code
 * to focus the Design view.
 */
export function useDesignUi(): [DesignUi, Update] {
  const [ui, setUi] = useState<DesignUi>(INITIAL);

  useEffect(() => {
    const onMessage = (e: MessageEvent<{ type?: string; state?: Partial<DesignUi> }>) => {
      if (e.data?.type === "hitlcad.ui.state") setUi({ ...INITIAL, ...e.data.state });
    };
    window.addEventListener("message", onMessage);
    toExtension({ type: "hitlcad.ui.ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const update = useCallback<Update>((patch, reveal = false) => {
    setUi((cur) => ({ ...cur, ...patch }));
    toExtension({ type: "hitlcad.ui", patch, reveal });
  }, []);
  return [ui, update];
}

/** The revision `ui.revision` names, else the latest successful one, else the latest. */
export function currentRevision(revisions: Revision[], chosen: number | null): Revision | null {
  if (chosen !== null) return revisions.find((r) => r.id === chosen) ?? null;
  return [...revisions].reverse().find((r) => r.report.status === "succeeded") ?? revisions[revisions.length - 1] ?? null;
}

/** The revision to compare `current` against: the chosen base, else the last successful one before it. */
export function baseRevision(revisions: Revision[], current: Revision | null, chosen: number | null): Revision | null {
  if (!current) return null;
  if (chosen !== null && chosen !== current.id) return revisions.find((r) => r.id === chosen) ?? null;
  const before = revisions.filter((r) => r.id < current.id);
  return [...before].reverse().find((r) => r.report.status === "succeeded") ?? before[before.length - 1] ?? null;
}
