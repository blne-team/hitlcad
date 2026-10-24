import type { DesignState } from "../api/types";

/** The design's build status and the panel's connection, one line under its name. */
export function StatusLine({ design, connected }: { design: DesignState; connected: boolean | null }) {
  const status = design.building ? "building" : design.error ? "failed" : "idle";
  const label = design.building ? "Building" : design.error ? "Problem with the design files" : "Up to date";
  return (
    <div className="statusline" title="Saving the design's files rebuilds what changed">
      <span className={`dot status-${status}`} aria-hidden />
      <span className={`status-text status-${status}`}>{label}</span>
      {design.error && <span className="muted status-error" title={design.error}>{design.error.slice(0, 160)}</span>}
      <span className={connected ? "conn ok" : connected === null ? "conn" : "conn fail"}>
        {connected ? "Live" : connected === null ? "Connecting" : "Reconnecting"}
      </span>
    </div>
  );
}
