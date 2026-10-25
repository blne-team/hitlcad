import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fileUrl } from "../api/client";
import type { Pick, Revision } from "../api/types";
import { useDesign } from "../hooks/useDesign";
import { useSelection } from "../hooks/useSelection";
import { useViewJson } from "../hooks/useViewJson";
import { type CadMarker, type CadMotion, exactShapes } from "../lib/cadView";
import { baseRevision, currentRevision, useDesignUi } from "../lib/designUi";
import { deliveryStatus } from "../lib/delivery";
import { toExtension } from "../lib/host";
import { selectionMarker } from "../lib/selection";
import { CadCanvas } from "./CadCanvas";
import { ExplodeSpread } from "./ExplodeSpread";
import { MotionControl } from "./MotionControl";
import { ReviewPanel } from "./ReviewPanel";
import { SelectionBox } from "./SelectionBox";
import { StatusLine } from "./StatusLine";

/** Last few segments of a long absolute path: /home/me/work/designs/hook → …/work/designs/hook */
function shortPath(path: string, keep = 3): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  return parts.length <= keep ? path : `…/${parts.slice(-keep).join("/")}`;
}

/** The revision's 3D view. */
function viewOf(designId: string, revision: Revision | null): { url: string | null } {
  const view = revision?.report.artifacts.find((a) => a.role === "view");
  return { url: revision && view ? fileUrl(designId, revision.id, view.path) : null };
}

/** The 3D editor tab of one design folder: the model, selection and pins. Its tabs are the Design view (InspectorView). */
export function DesignView({ id }: { id: string }) {
  const live = useDesign(id);
  const { state } = live;
  const [ui, setUi] = useDesignUi();
  // A view error belongs to the view it came from: another revision's view starts without one
  const [viewError, setViewError] = useState<{ url: string | null; message: string } | null>(null);
  const [pinError, setPinError] = useState<string | null>(null);
  const [motion, setMotion] = useState<CadMotion | null>(null);
  const [exploded, setExploded] = useState(false);
  const [spread, setSpread] = useState(1);

  const revisions = useMemo(() => state?.revisions ?? [], [state?.revisions]);
  const revision = currentRevision(revisions, ui.revision, ui.configuration);
  const revisionId = revision?.id ?? null;
  const base = baseRevision(revisions, revision, ui.base);

  const current = viewOf(id, revision);
  const { data, error: fetchError } = useViewJson(current.url);
  const ghost = useViewJson(ui.compare ? viewOf(id, base).url : null).data;
  const modelError = fetchError ?? (viewError?.url === current.url ? viewError.message : null);

  const describe = useCallback(async (picks: Pick[]) => (await api.setSelection(id, revisionId, picks)).items, [id, revisionId]);
  const sel = useSelection(describe, state?.selection);
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

  // Follow new revisions (of the configuration on screen) unless the user deliberately went back to an older one.
  const latestId = currentRevision(revisions, null, ui.configuration)?.id ?? null;
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

  const own = revisions.filter((r) => r.spec.configuration === ui.configuration);
  const idx = revision ? own.findIndex((r) => r.id === revision.id) : -1;
  const size = revision?.report.metrics?.size;

  return (
    <div className="design">
      <header className="stagebar">
        <div className="stage-title">
          <h1 title={state.name}>{state.name}</h1>
          <span className="design-path" title={state.path}>
            {shortPath(state.path)}
          </span>
          <span className="muted tiny">{state.backend}</span>
          {ui.configuration && <span className="config-chip">{ui.configuration}</span>}
          {size && (
            <span className="model-size" aria-label="Model size">
              {size.map((v) => v.toFixed(1)).join(" × ")} mm
            </span>
          )}
          <StatusLine design={state} connected={live.connected} />
        </div>
        <div className="stage-actions" role="toolbar" aria-label="Design">
          <div className="rev-switch">
            <button
              type="button"
              className="icon-button"
              aria-label="Previous revision"
              disabled={idx <= 0}
              onClick={() => setUi({ revision: own[idx - 1]?.id ?? null })}
            >
              ‹
            </button>
            <span className="rev-label">{revision ? `Revision ${revision.id} of ${revisions.length}` : "No revisions yet"}</span>
            <button
              type="button"
              className="icon-button"
              aria-label="Next revision"
              disabled={idx < 0 || idx >= own.length - 1}
              onClick={() => setUi({ revision: own[idx + 1]?.id ?? null })}
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
          <button
            type="button"
            className="icon-button full-screen"
            aria-label="Full screen"
            title="Full screen: hide VS Code's bars (Zen mode); again to bring them back"
            onClick={() => toExtension({ type: "hitlcad.fullscreen" })}
          >
            ⤢
          </button>
        </div>
      </header>

      <div className={`viewport${exploded ? " exploded" : ""}`}>
        {data !== null && !modelError && (
          <CadCanvas data={data} placing={ui.placing} markers={markers} ghost={ghost} motion={motion} spread={spread} onExplode={setExploded} onPick={onPick} onError={(message) => setViewError({ url: current.url, message })} />
        )}
        {!current.url && (
          <div className="viewport-message">
            {revision?.report.status === "failed" ? (
              <p>Revision {revision.id} failed to build. The error is under Results.</p>
            ) : state.building ? (
              <p>Building…</p>
            ) : (
              <p>No build yet. Ask your agent to declare the parts in design.json and write their code; every save builds.</p>
            )}
          </div>
        )}
        {modelError && <p className="viewport-message fail">{modelError}</p>}
        {pinError && <p className="viewport-message fail">{pinError}</p>}

        {data !== null && !modelError && revision && revision.report.motions.length > 0 && (
          <MotionControl key={revision.id} motions={revision.report.motions} onMotion={setMotion} />
        )}
        {exploded && <ExplodeSpread value={spread} onChange={setSpread} />}
        {ui.placing && <p className="viewport-hint">Click the surface to pin a note for your agent. Drag to orbit.</p>}
        {!ui.placing && data !== null && !sel.items.length && (
          <p className="viewport-hint select-hint">
            {exactShapes(data).size ? "Click a face, edge or vertex to select it; with the filter on Solid, a whole part." : "Click the model to select a point."} Shift-click adds more.
          </p>
        )}

        <div className="side">
          <ReviewPanel ctx={{ id, state, revision, ui, setUi, refresh: live.refresh }} />
          <SelectionBox items={sel.items} busy={sel.busy} error={sel.error} onClear={sel.clear} status={sel.items.length ? deliveryStatus(state) : null} />
        </div>
      </div>
    </div>
  );
}
