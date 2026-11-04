import type { CadMove } from "./motion";

/** What the print view draws shaded: triangles (x, y, z of each corner, flat) by how they are shaded. */
export type Shading = Partial<Record<Shade, number[]>>;

/**
 * The print view's shading file (`build.assemble.write_print_shading`): each printed part, each way it could lie
 * (as its plan lists them), with what its process found of it lying that way, shaded where the view would lay it so,
 * and the move there from where it lies now. Its process checks decide them; the view only draws them.
 */
export interface PrintShading {
  parts: { path: string; ways: (Shading & { up: number[]; current: boolean; move: number[] })[] }[];
}

/** A way up the human points at in a part's plan: the print view shows the part lying so. */
export interface PointedWay {
  revision: number;
  path: string;
  up: number[];
}

const same = (a: number[], b: number[]) => a.length === b.length && a.every((v, n) => Math.abs(v - b[n]!) < 1e-6);

/** The way each part is shown: the one pointed at for its part, else the way it prints. */
function shownWays(file: PrintShading, pointed: PointedWay | null) {
  return file.parts.map((part) => ({
    path: part.path,
    way: (pointed?.path === part.path && part.ways.find((w) => same(w.up, pointed.up))) || part.ways.find((w) => w.current)!,
  }));
}

/** What the print view shades: each part as it is shown. */
export function shadingOf(file: PrintShading, pointed: PointedWay | null): Shading {
  const out: Required<Shading> = { support: [], scar: [], bed: [] };
  for (const { way } of shownWays(file, pointed)) {
    for (const shade of Object.keys(out) as Shade[]) way[shade]?.forEach((v) => out[shade].push(v));
  }
  return out;
}

/** The part pointed at, turned the way pointed at (as the motion preview moves parts); none: each as it prints. */
export function pointedMove(file: PrintShading, pointed: PointedWay | null): CadMove[] | null {
  const moved = shownWays(file, pointed).filter(({ way }) => !way.current);
  return moved.length ? moved.map(({ path, way }) => ({ instances: [path], matrix: way.move })) : null;
}

export type Shade = "support" | "scar" | "bed";

/** How the print view shades them, and how the Print tab names them. */
export const SHADES: Record<Shade, { color: string; label: string }> = {
  support: { color: "#e03131", label: "needs support" },
  scar: { color: "#f59f00", label: "shows, and needs support: scarred" },
  bed: { color: "#1c7ed6", label: "lies on the bed" },
};
