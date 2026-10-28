import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fileUrl } from "../api/client";
import type { Pick, Revision } from "../api/types";
import { useDesign } from "../hooks/useDesign";
import { useShown } from "../hooks/useRevision";
import { useSelection } from "../hooks/useSelection";
import { useScene } from "../hooks/useScene";
import { type CadMarker, exactShapes } from "../lib/cadView";
import { rangeAt, shown } from "../lib/motion";
import { currentRevision, useDesignUi, type InspectorTab } from "../lib/designUi";
import { deliveryStatus } from "../lib/delivery";
import { toExtension } from "../lib/host";
import { selectionMarker } from "../lib/selection";
import { inspectionTargets, type InspectionAction } from "../lib/inspection";
import { CadCanvas } from "./CadCanvas";
import { ExplodeSpread } from "./ExplodeSpread";
import { MotionControl } from "./MotionControl";
import { SelectHint } from "./SelectHint";
import { ReviewPanel } from "./ReviewPanel";
import { SelectionBox } from "./SelectionBox";
import { StatusLine } from "./StatusLine";
import { InspectionControls } from "./InspectionControls";
import { MeasurementPanel } from "./MeasurementPanel";

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
  const [exploded, setExploded] = useState(false);
  const [spread, setSpread] = useState(1);
  const [reviewTab, setReviewTab] = useState<InspectorTab | null>(null);

  const revisions = useMemo(() => state?.revisions ?? [], [state?.revisions]);
  const { info, revision, base } = useShown(id, revisions, ui);
  const revisionId = info?.id ?? null;
  const motion = useMemo(() => shown(revision, ui.motion), [revision, ui.motion]);

  // Following the latest revision, the build running is drawn as soon as its parts are placed, before it is checked;
  // its results, motion and picks come with its revision.
  const running = state?.building ?? null;
  const building = ui.revision === null && running?.scene && running.configuration === ui.configuration ? running : null;
  const current = building?.scene ? { url: fileUrl(id, building.revision, building.scene) } : viewOf(id, revision);
  const { data, error: fetchError } = useScene(current.url, building?.started);
  const ghost = useScene(ui.compare ? viewOf(id, base).url : null).data;
  const modelError = fetchError ?? (viewError?.url === current.url ? viewError.message : null);

  const describe = useCallback(async (picks: Pick[]) => {
    if (!picks.length) setUi({ inspection: null });
    return (await api.setSelection(id, revisionId, picks)).items;
  }, [id, revisionId, setUi]);
  const sel = useSelection(describe, state?.selection);
  const openPins = useMemo(() => (state?.pins ?? []).filter((p) => !p.resolved), [state?.pins]);
  const markers = useMemo<CadMarker[]>(
    () => building ? [] : [
      // Pins move to each new build where their spot is found; a pin is drawn on the revision it is on.
      ...openPins
        .filter((p) => p.revision === revisionId)
        .map((p) => ({ id: p.id, kind: "pin" as const, point: p.point, instance: p.instance ?? null, normal: p.normal, label: p.note, active: p.id === ui.activePin })),
      ...sel.items
        .filter((i) => i.revision === null || i.revision === revisionId)
        .map(selectionMarker),
    ],
    [building, openPins, ui.activePin, sel.items, revisionId],
  );

  // Follow new revisions (of the configuration on screen) unless the user deliberately went back to an older one.
  const latestId = currentRevision(revisions, null, ui.configuration)?.id ?? null;
  useEffect(() => setUi({ revision: null }), [latestId, setUi]);

  const savePin = async (pick: Pick, note = "") => {
    const range = rangeAt(revision, ui.motion);  // the pick is as built; the pin says where the motion was
    const motion = range && ui.motion && ui.motion.at !== range.current ? { pair: range.pair, kind: range.kind, at: ui.motion.at } : null;
    const pin = await api.addPin(id, { ...pick, note, revision: revisionId, motion });
    setUi({ activePin: pin.id, tab: "pins" }, true); // the note is written in the Design view
    live.refresh();
  };
  const addPin = (pick: Pick) => {
    setPinError(null);
    return savePin(pick).catch((e: Error) => setPinError(e.message));
  };
  const onPick = (pick: Pick, additive: boolean) => {
    if (building) return;
    if (ui.placing) void addPin(pick);
    else {
      setUi({ inspection: null });
      sel.select(pick, additive);
    }
  };

  const selection = sel.items.filter((i) => i.revision === revisionId);
  const inspection = ui.inspection?.revision === revisionId ? ui.inspection : null;
  const targets = inspectionTargets(revisionId, inspection, sel.items);
  const inspect = (action: InspectionAction) => setUi({
    inspection: { revision: revisionId, instances: targets, action, sequence: (ui.inspection?.sequence ?? 0) + 1 },
  });

  if (!state) {
    return (
      <main className="design-empty">
        {live.error ? <p className="notice fail">{live.error}</p> : <p className="muted">Opening design…</p>}
      </main>
    );
  }

  const own = revisions.filter((r) => r.configuration === ui.configuration);
  const idx = info ? own.findIndex((r) => r.id === info.id) : -1;
  const size = building ? null : revision?.report.metrics?.size;

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
            <span className="rev-label">
              {building ? `Revision ${building.revision}, being checked` : info ? `Revision ${info.id} of ${revisions.length}` : "No revisions yet"}
            </span>
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
            disabled={data === null || building !== null}
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
          <CadCanvas data={data} placing={ui.placing && !building} markers={markers} ghost={ghost} motion={building ? null : motion} spread={spread} inspection={building ? null : inspection} onExplode={setExploded} onPick={onPick} onError={(message) => setViewError({ url: current.url, message })}
            tools={(activateInspection) => <InspectionControls count={building ? 0 : targets.length} onOpen={() => {
              activateInspection();
              setUi({ placing: false });
            }} onAction={inspect} onParameters={() => {
              inspect("focus");
              setReviewTab("parameters");
            }} />} />
        )}
        {!current.url && (
          <div className="viewport-message">
            {info?.status === "failed" ? (
              <p>Revision {info.id} failed to build. Results says why.</p>
            ) : state.building ? (
              <p>Building…</p>
            ) : (
              <p>No build yet. Ask your agent to declare the parts in design.json and write their code; every save builds.</p>
            )}
          </div>
        )}
        {modelError && <p className="viewport-message fail">{modelError}</p>}
        {pinError && <p className="viewport-message fail">{pinError}</p>}

        {data !== null && !modelError && !building && revision && revision.report.motions.length > 0 && (
          <MotionControl key={revision.id} designId={id} revision={revision} watched={state.watched} at={ui.motion}
            onMove={(motion) => setUi({ motion })} />
        )}
        {exploded && <ExplodeSpread value={spread} onChange={setSpread} />}
        {ui.placing && <p className="viewport-hint">Click the surface to pin a note for your agent. Drag to orbit.</p>}
        {!ui.placing && data !== null && !building && !sel.items.length && (
          <SelectHint>
            {exactShapes(data).size ? "Click a face, edge or vertex to select it; with the filter on Solid, a whole part." : "Click the model to select a point."} Shift-click adds more. Right-drag to pan; scroll to zoom.
          </SelectHint>
        )}

        <div className="side">
          <ReviewPanel ctx={{ id, state, revision, base, ui, setUi, refresh: live.refresh }} open={reviewTab} onToggle={setReviewTab} />
          <SelectionBox items={sel.items} busy={sel.busy} error={sel.error} onClear={sel.clear} status={sel.items.length ? deliveryStatus(state) : null}>
            {selection.length === 2 && revisionId !== null && !sel.busy && !ui.motion && (
              <MeasurementPanel key={`${revisionId}:${selection.map((i) => i.id).join(",")}`} designId={id} revision={revisionId}
                items={[selection[0]!, selection[1]!]} onPin={savePin} />
            )}
          </SelectionBox>
        </div>
      </div>
    </div>
  );
}
