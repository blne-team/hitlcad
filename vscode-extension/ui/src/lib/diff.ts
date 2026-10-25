import { diffLines as lineChanges } from "diff";

interface DiffLine {
  kind: " " | "+" | "-";
  text: string;
}

/** Line by line: what `after` keeps, adds and removes of `before`. */
export function diffLines(before: string, after: string): DiffLine[] {
  return lineChanges(before, after).flatMap((c) => {
    const kind = c.added ? "+" : c.removed ? "-" : " ";
    return c.value.replace(/\n$/, "").split("\n").map((text) => ({ kind, text }));
  });
}

/** Changed lines with `context` unchanged lines around them; `null` marks skipped stretches. */
export function hunks(lines: DiffLine[], context = 2): (DiffLine | null)[] {
  const keep = lines.map((l, i) => lines.slice(Math.max(0, i - context), i + context + 1).some((x) => x.kind !== " ") || l.kind !== " ");
  const out: (DiffLine | null)[] = [];
  lines.forEach((l, i) => {
    if (keep[i]) out.push(l);
    else if (out[out.length - 1] !== null) out.push(null);
  });
  return out;
}
