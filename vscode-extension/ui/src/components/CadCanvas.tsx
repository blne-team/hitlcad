import { useEffect, useLayoutEffect, useRef } from "react";
import { useDarkScheme } from "../hooks/useScheme";
import type { Pick } from "../api/types";
import { type CadBounds, type CadMarker, type CadMotion, CadView, exactShapes } from "../lib/cadView";

interface Props {
  /** A view JSON from the server (/api/files/cad or a revision's view.json). */
  data: unknown;
  /** Clicks place pins: a measure tool that is on is turned off. */
  placing?: boolean;
  markers: CadMarker[];
  /** A view JSON to show as a ghost, e.g. the revision being compared against. */
  ghost?: unknown;
  /** Instances moved along a mate's range of motion. */
  motion?: CadMotion | null;
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
  useLayoutEffect(() => {
    latest.current = { ...props, dark };
  });

  useEffect(() => {
    if (!stage.current) return;
    const now = latest.current;
    let cad: CadView;
    try {
      cad = new CadView(stage.current, props.data, now.dark, (pick, additive) => latest.current.onPick(pick, additive));
    } catch (e) {
      now.onError(e instanceof Error ? e.message : String(e)); // a view JSON the viewer cannot show
      return;
    }
    cad.setMarkers(now.markers);
    cad.setGhost(now.ghost ?? null);
    cad.setMotion(now.motion ?? null);
    view.current = cad;
    now.onReady?.(cad.bounds);
    return () => {
      view.current = null;
      cad.dispose();
    };
  }, [props.data]);

  useEffect(() => view.current?.setDark(dark), [dark]);
  useEffect(() => {
    if (props.placing) view.current?.stopTool(); // the next click places a pin, not a measurement
  }, [props.placing]);
  useEffect(() => view.current?.setMarkers(props.markers), [props.markers]);
  useEffect(() => view.current?.setGhost(props.ghost ?? null), [props.ghost]);
  useEffect(() => view.current?.setMotion(props.motion ?? null), [props.motion]);

  // Without an exact shape there are no faces, edges or vertices to filter picks by
  return <div ref={stage} className="cad-stage" data-exact={exactShapes(props.data).size > 0} />;
}
