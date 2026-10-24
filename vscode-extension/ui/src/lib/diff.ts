interface DiffLine {
  kind: " " | "+" | "-";
  text: string;
}

/** Line diff via longest common subsequence; fine for model files of a few hundred lines. */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i]![j] = a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: " ", text: a[i]! });
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) out.push({ kind: "-", text: a[i++]! });
    else out.push({ kind: "+", text: b[j++]! });
  }
  while (i < a.length) out.push({ kind: "-", text: a[i++]! });
  while (j < b.length) out.push({ kind: "+", text: b[j++]! });
  return out;
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
