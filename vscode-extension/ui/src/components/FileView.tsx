import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fileCadUrl } from "../api/client";
import type { Pick, SelectionItem } from "../api/types";
import { useSelection } from "../hooks/useSelection";
import { useViewJson } from "../hooks/useViewJson";
import { type CadBounds, exactShapes } from "../lib/cadView";
import { notifyHost, selectionMarker, selectionText } from "../lib/selection";
import { CadCanvas } from "./CadCanvas";
import { SelectionBox } from "./SelectionBox";

/** Viewer for any model file on the server's machine, which the server converts for three-cad-viewer. */
export function FileView({ path }: { path: string }) {
  const name = path.split(/[\\/]/).pop() || path;
  const { data, error: fetchError } = useViewJson(fileCadUrl(path));
  const [viewError, setViewError] = useState<string | null>(null);
  const loadError = fetchError ?? viewError;
  const [bounds, setBounds] = useState<CadBounds | null>(null);
  const described = useRef(new Map<string, SelectionItem>());

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
      <div className="viewport">
        {data !== null && !loadError && (
          <CadCanvas data={data} markers={markers} onPick={sel.select} onReady={setBounds} onError={setViewError} />
        )}
        {data === null && !loadError && <div className="viewport-message">Converting {name}…</div>}
        {loadError && <p className="viewport-message fail">Could not show {name}: {loadError}</p>}
        <header className="float stage-title">
          <h1 title={path}>{name}</h1>
          <div className="stage-meta">
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
        {data !== null && !loadError && !sel.items.length && (
          <p className="viewport-hint select-hint">
            {exactShapes(data).size ? "Click a face, edge or vertex to select it." : "Click the model to select a point."} Shift-click adds more.
          </p>
        )}
        <div className="side">
          <SelectionBox items={sel.items} busy={sel.busy} error={sel.error} onClear={sel.clear} />
        </div>
      </div>
    </div>
  );
}
