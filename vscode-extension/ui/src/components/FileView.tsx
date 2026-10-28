import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fileCadUrl } from "../api/client";
import type { Pick, SelectionItem } from "../api/types";
import { useSelection } from "../hooks/useSelection";
import { useScene } from "../hooks/useScene";
import { type CadBounds, exactShapes } from "../lib/cadView";
import { notifyHost, selectionMarker, selectionText } from "../lib/selection";
import { CadCanvas } from "./CadCanvas";
import { ExplodeSpread } from "./ExplodeSpread";
import { SelectHint } from "./SelectHint";
import { SelectionBox } from "./SelectionBox";
import { InspectionControls } from "./InspectionControls";
import { type Inspection, selectedInstances } from "../lib/inspection";

/** Viewer for any model file on the server's machine, which the server converts for three-cad-viewer. */
export function FileView({ path }: { path: string }) {
  const name = path.split(/[\\/]/).pop() || path;
  const { data, error: fetchError } = useScene(fileCadUrl(path));
  const [viewError, setViewError] = useState<string | null>(null);
  const loadError = fetchError ?? viewError;
  const [bounds, setBounds] = useState<CadBounds | null>(null);
  const described = useRef(new Map<string, SelectionItem>());
  const [exploded, setExploded] = useState(false);
  const [spread, setSpread] = useState(1);
  const [inspection, setInspection] = useState<Inspection | null>(null);

  const describe = useCallback(
    (picks: Pick[]) =>
      Promise.all(
        picks.map(async (pick) => {
          const key = JSON.stringify([pick.point, pick.component]);
          const hit = described.current.get(key);
          if (hit) return hit;
          const item = await api.describeFile(path, pick);
          described.current.set(key, item);
          return item;
        }),
      ),
    [path],
  );
  const sel = useSelection(describe);
  const text = selectionText(`In ${name}`, sel.items);
  useEffect(() => notifyHost(text), [text]); // "Copy for chat" in VS Code includes it
  const markers = useMemo(() => sel.items.map(selectionMarker), [sel.items]);

  const size = bounds ? bounds.max.map((v, i) => v - bounds.min[i]!) : null;

  return (
    <div className="design">
      <header className="stagebar">
        <div className="stage-title">
          <h1 title={path}>{name}</h1>
          <span className="design-path" title={path}>
            {path}
          </span>
          {size && (
            <span className="model-size" aria-label="Model size">
              {size.map((v) => v.toFixed(1)).join(" × ")} mm
            </span>
          )}
        </div>
      </header>
      <div className={`viewport${exploded ? " exploded" : ""}`}>
        {data !== null && !loadError && (
          <CadCanvas data={data} markers={markers} spread={spread} inspection={inspection} onExplode={setExploded} onPick={sel.select} onReady={setBounds} onError={setViewError}
            tools={(activateInspection) => <InspectionControls count={selectedInstances(sel.items).length} onOpen={activateInspection} onAction={(action) => setInspection({
              revision: null, instances: selectedInstances(sel.items), action, sequence: (inspection?.sequence ?? 0) + 1,
            })} />} />
        )}
        {data === null && !loadError && <div className="viewport-message">Converting {name}…</div>}
        {loadError && <p className="viewport-message fail">Could not show {name}: {loadError}</p>}
        {exploded && <ExplodeSpread value={spread} onChange={setSpread} />}
        {data !== null && !loadError && !sel.items.length && (
          <SelectHint>
            {exactShapes(data).size ? "Click a face, edge or vertex to select it." : "Click the model to select a point."} Shift-click adds more. Right-drag to pan; scroll to zoom.
          </SelectHint>
        )}
        <div className="side">
          <SelectionBox items={sel.items} busy={sel.busy} error={sel.error} onClear={sel.clear} />
        </div>
      </div>
    </div>
  );
}
