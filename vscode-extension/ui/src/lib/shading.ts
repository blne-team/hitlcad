/**
 * What the print view shades on its parts, where it lays them (`build.assemble.write_print_shading`): triangles
 * (x, y, z of each corner, flat) by how they are shaded. Their process checks decide them; the view only draws them.
 */
export type Shading = Partial<Record<Shade, number[]>>;

export type Shade = "support" | "bed";

/** How the print view shades them, and how the Print tab names them. */
export const SHADES: Record<Shade, { color: string; label: string }> = {
  support: { color: "#e03131", label: "needs support" },
  bed: { color: "#1c7ed6", label: "lies on the bed" },
};
