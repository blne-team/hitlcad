import { useCallback, useEffect, useState } from "react";
import type { RevisionInfo } from "../api/types";
import { toExtension } from "./host";
import type { Inspection } from "./inspection";

export type InspectorTab = "pins" | "structure" | "parameters" | "changes" | "report" | "bom" | "brief";

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
  /** The configuration on screen; null is the parameters as written. */
  configuration: string | null;
  /** Where a range of motion of a revision is in the view; null, or another revision's: as built. */
  motion: MotionAt | null;
  inspection: Inspection | null;
}

/** A range of motion (by its frames and kind) of one revision at one value: what the slider shows, and where a
 * pin placed then was. */
export interface MotionAt {
  revision: number;
  pair: string;
  kind: "angle" | "travel";
  at: number;
}

const INITIAL: DesignUi = {
  revision: null, base: null, compare: false, placing: false, activePin: null, tab: "pins", configuration: null, motion: null, inspection: null,
};

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

/** The revision `ui.revision` names, else the latest successful one of the configuration, else its latest. */
export function currentRevision(revisions: RevisionInfo[], chosen: number | null, configuration: string | null = null): RevisionInfo | null {
  if (chosen !== null) return revisions.find((r) => r.id === chosen) ?? null;
  const own = revisions.filter((r) => r.configuration === configuration);
  return [...own].reverse().find((r) => r.status === "succeeded") ?? own[own.length - 1] ?? null;
}

/** The revision to compare `current` against: the chosen base, else the last successful one before it. */
export function baseRevision(revisions: RevisionInfo[], current: RevisionInfo | null, chosen: number | null): RevisionInfo | null {
  if (!current) return null;
  if (chosen !== null && chosen !== current.id) return revisions.find((r) => r.id === chosen) ?? null;
  const before = revisions.filter((r) => r.id < current.id);
  return [...before].reverse().find((r) => r.status === "succeeded") ?? before[before.length - 1] ?? null;
}
