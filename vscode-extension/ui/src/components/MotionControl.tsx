import { useEffect, useMemo, useState } from "react";
import type { BuildReport } from "../api/types";
import type { CadMotion } from "../lib/cadView";

type Range = BuildReport["motions"][number];

interface Props {
  motions: Range[];
  onMotion: (motion: CadMotion | null) => void;
}

/** Move a mate's moving side through the range the build swept for collisions, in the 3D view. Keyed by the
 * revision: another revision's ranges start as built. */
export function MotionControl({ motions, onMotion }: Props) {
  const [chosen, setChosen] = useState(0);
  const range = motions[Math.min(chosen, motions.length - 1)];
  const [value, setValue] = useState(range?.current ?? 0);
  const motion = useMemo<CadMotion | null>(
    () =>
      range && value !== range.current
        ? { instances: range.moving, origin: range.origin, axis: range.axis, kind: range.kind, amount: range.sign * (value - range.current) }
        : null,
    [range, value],
  );
  useEffect(() => onMotion(motion), [motion, onMotion]);
  if (!range) return null;
  const unit = range.kind === "angle" ? "°" : " mm";
  const [lo, hi] = range.continuous ? [range.current, range.current + 360] : [range.start, range.end];
  return (
    <section className="motion-control" aria-label="Motion">
      {motions.length > 1 ? (
        <select aria-label="Mate" value={chosen} onChange={(e) => {
            const next = Number(e.target.value);
            setChosen(next);
            setValue(motions[next]?.current ?? 0);
          }}>
          {motions.map((m, i) => (
            <option key={`${m.pair}-${m.kind}`} value={i}>
              {m.pair} ({m.kind})
            </option>
          ))}
        </select>
      ) : (
        <span className="muted tiny">{range.pair}</span>
      )}
      <input type="range" aria-label="Move along the mate" min={lo} max={hi} step={(hi - lo) / 200} value={value}
        onChange={(e) => setValue(e.target.valueAsNumber)} />
      <span className="tiny">
        {value.toFixed(1)}
        {unit}
      </span>
      <button type="button" className="link-button" disabled={value === range.current} onClick={() => setValue(range.current)}>
        As built
      </button>
    </section>
  );
}
