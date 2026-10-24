import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fileUrl } from "../api/client";
import type { Pick, Revision } from "../api/types";
import { useDesign } from "../hooks/useDesign";
import { useSelection } from "../hooks/useSelection";
import { useViewJson } from "../hooks/useViewJson";
import type { CadMarker } from "../lib/cadView";
import { baseRevision, currentRevision, useDesignUi } from "../lib/designUi";
import { deliveryStatus } from "../lib/delivery";
import { selectionMarker, selectionText } from "../lib/selection";
import { CadCanvas } from "./CadCanvas";
import { SelectionBox } from "./SelectionBox";
import { StatusLine } from "./StatusLine";

/** Last few segments of a long absolute path: /home/me/work/designs/hook → …/work/designs/hook */
function shortPath(path: string, keep = 3): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  return parts.length <= keep ? path : `…/${parts.slice(-keep).join("/")}`;
}

/** The revision's 3D view and whether it came from a STEP (so clicks name faces, edges and vertices). */
function viewOf(designId: string, revision: Revision | null): { url: string | null; brep: boolean } {
  const view = revision?.report.artifacts.find((a) => a.role === "view");
  return {
    url: revision && view ? fileUrl(designId, revision.id, view.name) : null,
    brep: Boolean(revision?.report.artifacts.some((a) => a.role === "exchange")),
  };
}

/** The 3D editor tab of one design folder: the model, selection and pins. Its tabs are the Design view (InspectorView). */
export function DesignView({ id }: { id: string }) {
  const live = useDesign(id);
  const { state } = live;
  const [ui, setUi] = useDesignUi();
  const [viewError, setViewError] = useState<string | null>(null);
  const [pinError, setPinError] = useState<string | null>(null);

  const revisions = useMemo(() => state?.revisions ?? [], [state?.revisions]);
  const revision = currentRevision(revisions, ui.revision);
  const revisionId = revision?.id ?? null;
  const base = baseRevision(revisions, revision, ui.base);

  const current = viewOf(id, revision);
  const { data, error: fetchError } = useViewJson(current.url);
  const ghost = useViewJson(ui.compare ? viewOf(id, base).url : null).data;
  useEffect(() => setViewError(null), [current.url]);
  const modelError = fetchError ?? viewError;

  const describe = useCallback(async (picks: Pick[]) => (await api.setSelection(id, revisionId, picks)).items, [id, revisionId]);
  const designName = state?.name || "this design";
  const toText = useCallback(
    (items: Parameters<typeof selectionText>[1]) => {
      const revs = [...new Set(items.map((i) => i.revision).filter((r) => r !== null))];
      return selectionText(`In design ${designName}${revs.length === 1 ? ` (revision ${revs[0]})` : ""}`, items);
    },
    [designName],
  );
  const sel = useSelection(describe, toText, state?.selection);
  const openPins = useMemo(() => (state?.pins ?? []).filter((p) => !p.resolved), [state?.pins]);
  const markers = useMemo<CadMarker[]>(
    () => [
      // Pins move to each new build where their spot is found; a pin is drawn on the revision it is on.
      ...openPins
        .filter((p) => p.revision === revisionId)
        .map((p) => ({ id: p.id, kind: "pin" as const, point: p.point, normal: p.normal, label: p.note, active: p.id === ui.activePin })),
      ...sel.items
        .filter((i) => i.revision === null || i.revision === revisionId)
        .map(selectionMarker),
    ],
    [openPins, ui.activePin, sel.items, revisionId],
  );

  // Follow new revisions unless the user deliberately went back to an older one.
  const latestId = revisions[revisions.length - 1]?.id ?? null;
  useEffect(() => setUi({ revision: null }), [latestId, setUi]);

  const addPin = async (pick: Pick) => {
    setPinError(null);
    try {
      const pin = await api.addPin(id, { ...pick, revision: revisionId });
      setUi({ activePin: pin.id, tab: "pins" }, true); // the note is written in the Design view
      live.refresh();
    } catch (e) {
      setPinError(e instanceof Error ? e.message : String(e));
    }
  };
  const onPick = (pick: Pick, additive: boolean) => (ui.placing ? void addPin(pick) : sel.select(pick, additive));

  if (!state) {
    return (
      <main className="design-empty">
        {live.error ? <p className="notice fail">{live.error}</p> : <p className="muted">Opening design…</p>}
      </main>
    );
  }

  const idx = revision ? revisions.findIndex((r) => r.id === revision.id) : -1;
  const size = revision?.report.metrics?.size;

  return (
    <div className="design">
      <section className="stage">
        <header className="stage-head">
          <div className="stage-title">
            <h1 title={state.name}>{state.name}</h1>
            <div className="stage-meta">
              <span className="design-path" title={state.path}>
                {shortPath(state.path)}
              </span>
              <span className="muted tiny">{state.backend}</span>
              {size && (
                <span className="model-size" aria-label="Model size">
                  {size.map((v) => v.toFixed(1)).join(" × ")} mm
                </span>
              )}
            </div>
          </div>
          <div className="toolbar" role="toolbar" aria-label="Design">
            <div className="rev-switch">
              <button
                type="button"
                className="icon-button"
                aria-label="Previous revision"
                disabled={idx <= 0}
                onClick={() => setUi({ revision: revisions[idx - 1]?.id ?? null })}
              >
                ‹
              </button>
              <span className="rev-label">
                {revision ? `Revision ${revision.id} of ${revisions.length}` : "No revisions yet"}
              </span>
              <button
                type="button"
                className="icon-button"
                aria-label="Next revision"
                disabled={idx < 0 || idx >= revisions.length - 1}
                onClick={() => setUi({ revision: revisions[idx + 1]?.id ?? null })}
              >
                ›
              </button>
            </div>
            <button
              type="button"
              className={`toggle pin-toggle${ui.placing ? " on" : ""}`}
              aria-pressed={ui.placing}
              disabled={data === null}
              onClick={() => setUi({ placing: !ui.placing })}
            >
              {ui.placing ? "Placing pins" : "Place pins"}
            </button>
            <button
              type="button"
              className={`toggle compare-toggle${ui.compare ? " on" : ""}`}
              aria-pressed={ui.compare}
              disabled={revisions.length < 2}
              title="Show an earlier revision as a ghost and list what changed"
              onClick={() => setUi(ui.compare ? { compare: false } : { compare: true, tab: "changes" }, !ui.compare)}
            >
              Compare
            </button>
          </div>
        </header>

        <div className="viewport">
          {data !== null && !modelError && (
            <CadCanvas data={data} brep={current.brep} placing={ui.placing} markers={markers} ghost={ghost} onPick={onPick} onError={setViewError} />
          )}
          {!current.url && (
            <div className="viewport-message">
              {revision?.report.status === "failed" ? (
                <p>Revision {revision.id} failed to build. The error is under Checks.</p>
              ) : state.building ? (
                <p>Building…</p>
              ) : (
                <p>No model yet. Ask your agent to write model.py, or save it to build.</p>
              )}
            </div>
          )}
          {modelError && <p className="viewport-message fail">{modelError}</p>}
          {pinError && <p className="viewport-message fail">{pinError}</p>}
          {ui.placing && <p className="viewport-hint">Click the surface to pin a note for your agent. Drag to orbit.</p>}
          {!ui.placing && data !== null && !sel.items.length && (
            <p className="viewport-hint select-hint">
              {current.brep ? "Click a face, edge or vertex to select it." : "Click the model to select a point."} Shift-click adds more.
            </p>
          )}
          <SelectionBox items={sel.items} text={sel.text} busy={sel.busy} error={sel.error} onClear={sel.clear} status={sel.items.length ? deliveryStatus(state) : null} />
        </div>
      </section>

      <StatusLine design={state} connected={live.connected} />
    </div>
  );
}
