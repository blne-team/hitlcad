import type { BuildReport, Revision } from "../api/types";
import type { MotionAt } from "./designUi";

/** A range of motion as the build swept it: about fixed axes (`drives`), or a loop's solved positions (`poses`). */
export type MotionRange = BuildReport["motions"][number];

/** A rigid motion from as built, 3 × 4 by rows, as a report gives a loop's poses. */
export type Rigid = number[];

/** Instances moved together by one rigid motion. */
export interface CadMove {
  instances: string[];
  matrix: Rigid;
}

/** The range `at` names, if it is of `revision`. */
export function rangeAt(revision: Revision | null, at: MotionAt | null): MotionRange | null {
  if (!revision || !at || at.revision !== revision.id) return null;
  return revision.report.motions.find((m) => m.pair === at.pair && m.kind === at.kind) ?? null;
}

/** What `at` moves in `revision`'s view; null: everything as built. */
export function shown(revision: Revision | null, at: MotionAt | null): CadMove[] | null {
  const range = rangeAt(revision, at);
  return range && at && at.at !== range.current ? movesAt(range, at.at) : null;
}

/** The slider's span: a turn about a fixed axis all the way round from where it sits; anything else as swept. */
export function extent(range: MotionRange): [number, number] {
  return range.continuous && range.poses.length === 0 ? [range.current, range.current + 360] : [range.start, range.end];
}

/** What moves, and where to, with the mate at `value`: each drive about or along its own axis, or a loop at the
 * solved position nearest `value` (only what was checked is shown). */
export function movesAt(range: MotionRange, value: number): CadMove[] {
  if (range.poses.length > 0) {
    const i = nearest(range.values, value);
    return range.poses.map((p) => ({ instances: p.instances, matrix: p.frames[i] ?? STILL }));
  }
  const delta = value - range.current;
  return range.drives.map((d) => ({
    instances: d.instances,
    matrix: d.kind === "angle" ? turn(d.origin, d.axis, d.rate * delta) : slide(d.axis, d.rate * delta),
  }));
}

type Vec3 = [number, number, number];

const STILL: Rigid = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0];

function nearest(values: number[], value: number): number {
  let best = 0;
  values.forEach((v, i) => {
    if (Math.abs(v - value) < Math.abs((values[best] ?? v) - value)) best = i;
  });
  return best;
}

function unit([x, y, z]: Vec3): Vec3 {
  const n = Math.hypot(x, y, z);
  return [x / n, y / n, z / n];
}

/** The turn by `degrees` about the line through `origin` along `axis`. */
function turn(origin: Vec3, axis: Vec3, degrees: number): Rigid {
  const [x, y, z] = unit(axis);
  const a = (degrees * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a), k = 1 - c;
  const rows: [Vec3, Vec3, Vec3] = [
    [c + x * x * k, x * y * k - z * s, x * z * k + y * s],
    [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
    [z * x * k - y * s, z * y * k + x * s, c + z * z * k],
  ];
  const [ox, oy, oz] = origin;
  return rows.flatMap(([rx, ry, rz], i) => [rx, ry, rz, (i === 0 ? ox : i === 1 ? oy : oz) - (rx * ox + ry * oy + rz * oz)]);
}

/** The slide by `mm` along `axis`. */
function slide(axis: Vec3, mm: number): Rigid {
  const [x, y, z] = unit(axis);
  return [1, 0, 0, x * mm, 0, 1, 0, y * mm, 0, 0, 1, z * mm];
}
