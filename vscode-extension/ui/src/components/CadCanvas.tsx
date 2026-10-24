import { useEffect, useRef } from "react";
import { useDarkScheme } from "../hooks/useScheme";
import type { Pick } from "../api/types";
import { type CadBounds, type CadMarker, CadView } from "../lib/cadView";

interface Props {
  /** A view JSON from the server (/api/files/cad or a revision's view.json). */
  data: unknown;
  /** STEP-based: clicks resolve to faces, edges, vertices or parts. */
  brep: boolean;
  /** Clicks place pins: a measure tool that is on is turned off. */
  placing?: boolean;
  markers: CadMarker[];
  /** A view JSON to show as a ghost, e.g. the revision being compared against. */
  ghost?: unknown;
  /** A click on the model; `additive` when shift was held. */
  onPick: (pick: Pick, additive: boolean) => void;
  onReady?: (bounds: CadBounds | null) => void;
  onError: (message: string) => void;
}

/** The 3D view of every hitlcad panel: three-cad-viewer, built once per model, with markers and ghost on top. */
export function CadCanvas(props: Props) {
  const stage = useRef<HTMLDivElement | null>(null);
  const view = useRef<CadView | null>(null);
  const dark = useDarkScheme();
  // The viewer lives outside React: read the latest props through a ref so it is rebuilt only for a new model.
  const latest = useRef({ ...props, dark });
  latest.current = { ...props, dark };

  useEffect(() => {
    if (!stage.current) return;
    const now = latest.current;
    let cad: CadView;
    try {
      cad = new CadView(stage.current, props.data, now.dark, props.brep, (pick, additive) => latest.current.onPick(pick, additive));
      cad.setMarkers(now.markers);
      cad.setGhost(now.ghost ?? null);
    } catch (e) {
      now.onError(e instanceof Error ? e.message : String(e));
      return;
    }
    view.current = cad;
    now.onReady?.(cad.bounds);
    return () => {
      view.current = null;
      cad.dispose();
    };
  }, [props.data, props.brep]);

  useEffect(() => view.current?.setDark(dark), [dark]);
  useEffect(() => {
    if (props.placing) view.current?.stopTool(); // the next click places a pin, not a measurement
  }, [props.placing]);
  useEffect(() => view.current?.setMarkers(props.markers), [props.markers]);
  useEffect(() => view.current?.setGhost(props.ghost ?? null), [props.ghost]);

  return <div ref={stage} className="cad-stage" data-brep={props.brep} />;
}
