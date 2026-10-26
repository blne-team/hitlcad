import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Revision, RevisionInfo } from "../api/types";
import { baseRevision, currentRevision, type DesignUi } from "../lib/designUi";

/** Revisions read in full, by design and id: a revision never changes once built. */
const loaded = new Map<string, Revision>();

/** A revision in full (what it built from and its report), fetched once; null until it arrives. */
export function useRevision(designId: string, revisionId: number | null): Revision | null {
  const key = revisionId === null ? null : `${designId}/${revisionId}`;
  const [, arrived] = useState(0);
  useEffect(() => {
    if (key === null || loaded.has(key)) return;
    let live = true;
    api
      .revision(designId, revisionId!)
      .then((rev) => {
        loaded.set(key, rev);
        if (live) arrived((n) => n + 1);
      })
      .catch(() => undefined); // the design's state lists it; a revision that will not load shows as none
    return () => {
      live = false;
    };
  }, [designId, revisionId, key]);
  return key === null ? null : (loaded.get(key) ?? null);
}

/** The revision on screen as the design lists it, it in full, and the revision it is compared with in full. */
export function useShown(designId: string, revisions: RevisionInfo[], ui: DesignUi) {
  const info = currentRevision(revisions, ui.revision, ui.configuration);
  const revision = useRevision(designId, info?.id ?? null);
  const base = useRevision(designId, baseRevision(revisions, info, ui.base)?.id ?? null);
  return { info, revision, base };
}
