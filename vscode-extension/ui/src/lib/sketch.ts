type XY = [number, number];

/** A segment of a sketch's loop in its own XY, in mm: an arc by its start, middle and end; a curve as points. */
export type SketchSegment =
  | { kind: "line" | "curve"; points: XY[] }
  | { kind: "arc"; center: XY; radius: number; points: [XY, XY, XY] }
  | { kind: "circle"; center: XY; radius: number };

/** Where a shape lies in its part, as three-cad-viewer's `loc`: a position and a quaternion. */
export type Loc = [[number, number, number], [number, number, number, number]];

/** A sketch a part file drew, as its build recorded it (`build123d/entry.py`'s `Drawn`): each face's loops, outer
 * first, the frames in its part it was placed at (by rows), and the line of the design's code it ran from. Its
 * part is drawn at each of `placements`: a viewer group of the part's first shape, and where that shape lies in it. */
export interface SketchView {
  id: string;
  part: string;
  file: string;
  line: number;
  planes: number[][][];
  faces: SketchSegment[][][];
  placements: { group: string; loc: Loc }[];
}

export const sketchLabel = (sketch: SketchView) => `${sketch.part} · ${sketch.file}:${sketch.line}`;

const ARC_STEP = Math.PI / 36;
const turn = (angle: number) => ((angle % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);

function segmentPoints(segment: SketchSegment): XY[] {
  if (!("center" in segment)) return segment.points;
  const { center: [cx, cy], radius } = segment;
  const at = (angle: number): XY => [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
  if (segment.kind === "circle") return Array.from({ length: 73 }, (_, n) => at(n * ARC_STEP));
  const [start, middle, end] = segment.points.map(([x, y]) => Math.atan2(y - cy, x - cx)) as [number, number, number];
  // Counter-clockwise when its middle comes before its end that way round
  const sweep = turn(middle - start) < turn(end - start) ? turn(end - start) : turn(end - start) - 2 * Math.PI;
  const steps = Math.max(2, Math.ceil(Math.abs(sweep) / ARC_STEP));
  return Array.from({ length: steps + 1 }, (_, n) => at(start + (sweep * n) / steps));
}

/** Each loop of a sketch as one polyline in its own XY. */
export function sketchLoops(sketch: SketchView): XY[][] {
  return sketch.faces.flat().map((loop) => loop.flatMap((segment, n) => segmentPoints(segment).slice(n ? 1 : 0)));
}
