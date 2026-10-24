import * as path from "node:path";

export const DESIGN_FILE = "design.json";

/**
 * Walk up from `file` (a file or folder) to the nearest folder containing design.json.
 * Never goes above `stopDir` (the workspace root) when the file is inside it; otherwise
 * walks to the filesystem root. Returns the design folder or null.
 */
export function findDesignDir(file: string, stopDir: string | null, exists: (p: string) => boolean): string | null {
  let dir = path.resolve(file);
  if (!exists(dir) || !isDirLike(dir, exists)) dir = path.dirname(dir);
  const stop = stopDir ? path.resolve(stopDir) : null;
  const inside = stop !== null && isWithin(dir, stop);
  for (;;) {
    if (exists(path.join(dir, DESIGN_FILE))) return dir;
    if (inside && dir === stop) return null;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

/** True when `child` is `parent` or below it. */
export function isWithin(child: string, parent: string): boolean {
  const rel = path.relative(path.resolve(parent), path.resolve(child));
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

function isDirLike(p: string, exists: (p: string) => boolean): boolean {
  // A path is treated as a folder if it has a design.json or no extension; callers pass files normally.
  return exists(path.join(p, DESIGN_FILE)) || path.extname(p) === "";
}
