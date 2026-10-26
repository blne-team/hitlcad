import { useState } from "react";
import { api } from "../api/client";
import type { DesignState, Revision } from "../api/types";
import type { MotionAt } from "../lib/designUi";
import { extent, type MotionRange, rangeAt } from "../lib/motion";

interface Props {
  designId: string;
  revision: Revision;
  watched: DesignState["watched"];
  /** Where the slider is (`DesignUi.motion`): another revision's, or none, is as built. */
  at: MotionAt | null;
  onMove: (at: MotionAt | null) => void;
}

/** Move what a mate moves through the range the build swept for collisions, in the 3D view: gears it drives too, or
 * the rest of its loop at the positions solved. */
export function MotionControl({ designId, revision, watched, at, onMove }: Props) {
  const motions = revision.report.motions;
  const [picked, setPicked] = useState(0);
  const moved = rangeAt(revision, at);
  const range = moved ?? motions[Math.min(picked, motions.length - 1)];
  if (!range) return null;
  const value = moved && at ? at.at : range.current;
  const unit = range.kind === "angle" ? "°" : " mm";
  const [lo, hi] = extent(range);
  const move = (to: number) => onMove(to === range.current ? null : { revision: revision.id, pair: range.pair, kind: range.kind, at: to });
  return (
    <section className="motion-control" aria-label="Motion">
      {motions.length > 1 ? (
        <select aria-label="Mate" value={motions.indexOf(range)} onChange={(e) => {
            setPicked(Number(e.target.value));
            onMove(null);
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
        onChange={(e) => move(e.target.valueAsNumber)} />
      <span className="tiny">
        {value.toFixed(1)}
        {unit}
      </span>
      <button type="button" className="link-button" disabled={value === range.current} onClick={() => onMove(null)}>
        As built
      </button>
      {range.key && <LoopReview designId={designId} revision={revision} range={range} on={watched.find((w) => w.key === range.key)?.revision} />}
    </section>
  );
}

/** A loop is checked only at the positions it was solved at: the human watches it move, and says so, to pass it
 * (a new build, whose result rests on that); or takes it back. */
function LoopReview({ designId, revision, range, on }: { designId: string; revision: Revision; range: MotionRange; on: number | undefined }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const result = revision.report.results.find((r) => r.analysis === "motion" && r.subject === range.mate);
  const positions = result?.values.positions;
  const review = (watch: boolean) => {
    setBusy(true);
    setError(null);
    (watch ? api.watchMotion : api.unwatchMotion)(designId, revision.id, range.key)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };
  return on !== undefined && result?.code === "motion.watched" ? (
    <span className="tiny" title={result.detail}>
      ✓ watched (rev {on}){" "}
      <button type="button" className="link-button" disabled={busy} onClick={() => review(false)}>
        Undo
      </button>
    </span>
  ) : (
    <span className="tiny" title={result?.detail}>
      {result?.code === "motion.sampled" ? `clear at ${String(positions)} positions, not between them` : "checked at solved positions"}{" "}
      {result?.code === "motion.sampled" && (
        <button type="button" className="link-button" disabled={busy} onClick={() => review(true)}>
          Watched it
        </button>
      )}
      {error && <span className="fail"> {error}</span>}
    </span>
  );
}
