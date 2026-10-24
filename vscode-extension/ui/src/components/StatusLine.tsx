import type { DesignState } from "../api/types";

export function StatusLine({ design, connected }: { design: DesignState; connected: boolean | null }) {
  const status = design.building ? "building" : design.error ? "failed" : "idle";
  const label = design.building ? "Building" : design.error ? "Problem with the design files" : "Up to date";
  return (
    <footer className="statusline">
      <span className={`dot status-${status}`} aria-hidden />
      <span className={`status-text status-${status}`}>{label}</span>
      {design.error && <span className="muted" title={design.error}>{design.error.slice(0, 160)}</span>}
      <span className="spacer" />
      <span className="muted">Saving the design's files rebuilds what changed.</span>
      <span className={connected ? "conn ok" : connected === null ? "conn" : "conn fail"}>
        {connected ? "Live" : connected === null ? "Connecting" : "Reconnecting"}
      </span>
    </footer>
  );
}
