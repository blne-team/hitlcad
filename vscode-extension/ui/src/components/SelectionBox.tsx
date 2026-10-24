import type { SelectionItem } from "../api/types";
import { copyForChat, hasPoint } from "../lib/selection";

interface Props {
  items: SelectionItem[];
  text: string;
  busy: boolean;
  error: string | null;
  onClear: () => void;
  /** For a design's selection: whether the agent has it yet. */
  status?: { sent: boolean; text: string } | null;
}

/** Compact overlay listing what the human selected, with actions that hand it to the agent's chat. */
export function SelectionBox({ items, text, busy, error, onClear, status }: Props) {
  if (!items.length && !busy && !error) return null;
  return (
    <section className="selection-box" aria-label="Selection" aria-live="polite">
      <header>
        <span className="sel-title">Selection</span>
        {busy && <span className="spinner" aria-label="Describing" />}
        <span className="muted tiny">{items.length ? `${items.length} item${items.length === 1 ? "" : "s"}` : ""}</span>
      </header>
      {items.length > 0 && (
        <ol>
          {items.map((it, i) => (
            <li key={it.id}>
              <span className="sel-badge" aria-hidden>
                {i + 1}
              </span>
              <span>
                {it.label}
                {!hasPoint(it.label) && <span className="muted tiny"> at ({it.point.map((v) => v.toFixed(1)).join(", ")})</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
      {error && <p className="notice fail">{error}</p>}
      {status && <p className={`delivery tiny${status.sent ? " sent" : ""}`}>{status.text}</p>}
      <div className="actions">
        <button
          type="button"
          className="button small primary"
          disabled={!text}
          title="Copy the selections and open pins of every design in the workspace"
          onClick={copyForChat}
        >
          Copy for chat
        </button>
        <button type="button" className="button small" disabled={!items.length} onClick={onClear}>
          Clear
        </button>
        <span className="muted tiny">Shift-click adds, Esc clears</span>
      </div>
    </section>
  );
}
