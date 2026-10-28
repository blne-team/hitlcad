import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Shapes } from "three-cad-viewer";
import { useDarkScheme } from "../hooks/useScheme";
import type { Pick } from "../api/types";
import { type CadBounds, type CadMarker, type CadMotion, CadView, exactShapes } from "../lib/cadView";
import type { Inspection } from "../lib/inspection";

interface Props {
  /** A scene from the server (/api/files/cad or a revision's scene.json), with its part views (`loadScene`). */
  data: Shapes;
  /** Clicks place pins: a measure tool that is on is turned off. */
  placing?: boolean;
  markers: CadMarker[];
  /** A scene to show as a ghost, e.g. the revision being compared against. */
  ghost?: Shapes | null;
  /** Instances moved along a mate's range of motion. */
  motion?: CadMotion | null;
  /** How far the toolbar's explode pulls the shapes apart, times their distance from the model's middle. */
  spread?: number;
  inspection?: Inspection | null;
  /** Extra toolbar controls; activating inspection returns clicks from measurement tools to selection. */
  tools?: (activateInspection: () => void) => ReactNode;
  /** The toolbar's explode turned on or off; a new model starts as built. */
  onExplode?: (on: boolean) => void;
  /** A click on the model; `additive` when shift was held. */
  onPick: (pick: Pick, additive: boolean) => void;
  onReady?: (bounds: CadBounds | null) => void;
  onError: (message: string) => void;
}

/** The 3D view of every hitlcad panel: three-cad-viewer, built once per model, with markers and ghost on top. */
export function CadCanvas(props: Props) {
  const stage = useRef<HTMLDivElement | null>(null);
  const view = useRef<CadView | null>(null);
  const [toolbar, setToolbar] = useState<{
    element: HTMLElement; gridMenu: HTMLElement; activateInspection: () => void;
    setPan: (on: boolean) => void; setGridAtBase: (on: boolean) => void;
    panning: boolean; gridAtBase: boolean;
  } | null>(null);
  const panning = toolbar?.panning ?? false;
  const dark = useDarkScheme();
  // The viewer lives outside React: read the latest props through a ref so it is rebuilt only for a new model.
  const latest = useRef({ ...props, dark });
  useLayoutEffect(() => {
    latest.current = { ...props, dark };
  });

  useEffect(() => {
    if (!stage.current) return;
    const now = latest.current;
    let cad: CadView;
    try {
      cad = new CadView(
        stage.current,
        props.data,
        now.dark,
        (pick, additive) => latest.current.onPick(pick, additive),
        (on) => latest.current.onExplode?.(on),
        (panning) => setToolbar((current) => current ? { ...current, panning } : current),
      );
    } catch (e) {
      now.onError(e instanceof Error ? e.message : String(e)); // a scene the viewer cannot show
      return;
    }
    cad.setMarkers(now.markers);
    cad.setGhost(now.ghost ?? null);
    cad.setMotion(now.motion ?? null);
    if (now.spread !== undefined) cad.setSpread(now.spread);
    if (now.inspection) cad.inspect(now.inspection);
    now.onExplode?.(false);
    view.current = cad;
    setToolbar({
      element: cad.toolbar, gridMenu: cad.gridMenu, activateInspection: () => cad.activateInspection(),
      setPan: (on) => cad.setPan(on), setGridAtBase: (on) => cad.setGridAtBase(on),
      panning: false, gridAtBase: true,
    });
    now.onReady?.(cad.bounds);
    return () => {
      view.current = null;
      cad.dispose();
    };
  }, [props.data]);

  useEffect(() => view.current?.setDark(dark), [dark]);
  useEffect(() => {
    if (props.placing) view.current?.activateInspection(); // the next click places a pin
  }, [props.placing]);
  useEffect(() => view.current?.setMarkers(props.markers), [props.markers]);
  useEffect(() => view.current?.setGhost(props.ghost ?? null), [props.ghost]);
  useEffect(() => view.current?.setMotion(props.motion ?? null), [props.motion]);
  useEffect(() => {
    if (props.spread !== undefined) view.current?.setSpread(props.spread);
  }, [props.spread]);
  useEffect(() => {
    if (props.inspection) view.current?.inspect(props.inspection);
    else view.current?.clearInspection();
  }, [props.inspection]);

  // Without an exact shape there are no faces, edges or vertices to filter picks by
  return <>
    <div ref={stage} className="cad-stage" data-exact={exactShapes(props.data).size > 0} data-panning={panning} />
    {toolbar && createPortal(<div className="canvas-tools">
      <button type="button" className="viewer-tool-button pan-button" aria-label="Pan view" aria-pressed={panning}
        title="Pan view: drag to move the model. Click again to orbit. You can also right-drag to pan."
        onClick={() => toolbar.setPan(!panning)}>
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 3v18M3 12h18M8 7l4-4 4 4M8 17l4 4 4-4M7 8l-4 4 4 4M17 8l4 4-4 4" />
        </svg>
      </button>
      {props.tools?.(toolbar.activateInspection)}
    </div>, toolbar.element)}
    {toolbar && createPortal(<label className="grid-base-option" title="Place the XY grid at the model’s lowest Z. Uncheck to use Z = 0.">
      <input type="checkbox" checked={toolbar.gridAtBase} onChange={(e) => {
        const gridAtBase = e.target.checked;
        setToolbar((current) => current ? { ...current, gridAtBase } : current);
        toolbar.setGridAtBase(e.target.checked);
      }} />
      <span>At model base</span>
    </label>, toolbar.gridMenu)}
    {panning && <p className="viewport-hint pan-hint">Drag to move the view. Click Pan again to orbit.</p>}
  </>;
}
