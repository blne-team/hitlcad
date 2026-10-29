import type { DesignState } from "../api/types";

/** Who made an edit, as a design's changes record it (`DocumentChanged.origin`). */
function who(origin: string): string {
  if (origin === "panel") return "you, in the panel";
  if (origin.startsWith("agent:")) return origin.startsWith("agent:codex:") ? "Codex" : "Claude Code";
  return origin === "disk" ? "a save to the folder" : "hitlcad";
}

/** Edits that changed the same text at once: the later one was kept, so the human looks at what it kept. Shown
 * until dismissed (`seen`: the version dismissed up to). */
export function CrossedEdits({ state, seen, onSeen }: { state: DesignState; seen: number; onSeen: (version: number) => void }) {
  const unseen = state.crossed.filter((x) => x.version > seen);
  if (!unseen.length) return null;
  return (
    <section className="notice crossed" role="status" aria-label="Edits that crossed">
      <p>Two edits changed the same text at once, and the later one was kept. Check that it is what you want:</p>
      <ul>
        {unseen.flatMap((x) =>
          x.crossed.map((c) => (
            <li key={`${x.version}:${c.path}:${c.line}`}>
              <strong>{c.path}{c.line ? ` line ${c.line}` : ""}</strong>
              {c.now && <code>{c.now}</code>}
              <span className="muted"> {[x.origin, ...x.others].map(who).join(" and ")}</span>
            </li>
          )),
        )}
      </ul>
      <button type="button" className="link-button" onClick={() => onSeen(Math.max(...unseen.map((x) => x.version)))}>
        Dismiss
      </button>
    </section>
  );
}
