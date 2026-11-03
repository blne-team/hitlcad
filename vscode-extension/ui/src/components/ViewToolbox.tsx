import type { ViewPlane } from "../lib/cadView";
import { sketchLabel, type SketchView } from "../lib/sketch";

/**
 * The view's own toolbox, floating down the right-hand side: 3D or 2D, and the recorded sketches shown on the model.
 * In 2D the viewer's direction buttons choose what it looks along, and its resize button fits the view again.
 */
export function ViewToolbox({ sketches, looking, shown, onLook, onShown }: {
  sketches: SketchView[];
  looking: ViewPlane | null;
  shown: boolean;
  onLook: (plane: ViewPlane | null) => void;
  onShown: (shown: boolean) => void;
}) {
  return <div className="view-toolbox" role="toolbar" aria-label="View" aria-orientation="vertical">
    <button type="button" className="viewer-tool-button view-mode" aria-label="3D view" aria-pressed={looking === null}
      title="3D: orbit the model" onClick={() => onLook(null)}>3D</button>
    <button type="button" className="viewer-tool-button view-mode" aria-label="2D view" aria-pressed={looking !== null}
      title="2D: look straight at a sketch, or along a view direction of the toolbar; drag to pan, scroll to zoom"
      onClick={() => {
        if (!looking) onLook(sketches[0] ? { sketch: sketches[0] } : { direction: "top" });
      }}>2D</button>
    <span className="view-toolbox-rule" aria-hidden="true" />
    <button type="button" className="viewer-tool-button" aria-label="Show sketches" aria-pressed={shown} disabled={!sketches.length}
      title={sketches.length ? "Show the sketches the part files drew, on the model" : "No sketches: the part files draw none with BuildSketch"}
      onClick={() => onShown(!shown)}>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M3 17l6 4 12-8-6-4z" /><path d="M8 13.5l3 2 4-2.7" /><path d="M14 3l4 2.5-5 7.5-2.5.5.5-2.5z" />
      </svg>
    </button>
  </div>;
}

/** In the viewer's toolbar after its view directions: a recorded sketch to look straight at, in 2D. */
export function SketchPicker({ sketches, looking, onLook }: {
  sketches: SketchView[];
  looking: ViewPlane | null;
  onLook: (plane: ViewPlane) => void;
}) {
  return <select className="sketch-picker" aria-label="Sketch" title="Look straight at a sketch a part file drew (2D)"
    value={looking && "sketch" in looking ? looking.sketch.id : ""}
    onChange={(e) => onLook({ sketch: sketches.find((sketch) => sketch.id === e.target.value)! })}>
    <option value="" disabled>Sketch…</option>
    {sketches.map((sketch) => <option key={sketch.id} value={sketch.id}>{sketchLabel(sketch)}</option>)}
  </select>;
}
