import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Pin } from "../api/types";
import { copyForChat } from "../lib/selection";

interface Props {
  designId: string;
  pins: Pin[];
  /** The revision on screen; pins on another one were not found in it. */
  revision: number | null;
  activePin: string | null;
  setActivePin: (id: string | null) => void;
  onPinMode: () => void;
  canPin: boolean;
  onChanged: () => void;
  /** Whether the agent has the open pins yet. */
  status: { sent: boolean; text: string } | null;
}

/** Notes pinned to the model. Open pins go to the agent with the human's next message (the agents' prompt hook). */
export function PinsPanel({ designId, pins, revision, activePin, setActivePin, onPinMode, canPin, onChanged, status }: Props) {
  const [error, setError] = useState<string | null>(null);
  const open = pins.filter((p) => !p.resolved);
  const resolved = pins.filter((p) => p.resolved);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="pins-panel">
      <p className="muted">
        Pin notes to the model, then ask your agent in its chat, e.g. "address my pins". Open pins and your selection
        go to Claude Code or Codex with your next message. <b>Copy for chat</b> copies those of every design, to paste
        anywhere.
      </p>
      {open.length > 0 && (
        <div className="actions">
          {status && <span className={`delivery tiny${status.sent ? " sent" : ""}`}>{status.text}</span>}
          <button type="button" className="button small" onClick={copyForChat}>
            Copy for chat
          </button>
        </div>
      )}
      {open.length === 0 ? (
        <p className="hint">
          No open pins.{" "}
          {canPin && (
            <button type="button" className="link-button" onClick={onPinMode}>
              Place pins
            </button>
          )}
        </p>
      ) : (
        <ol className="pins">
          {open.map((p, i) => (
            <PinRow
              key={p.id}
              pin={p}
              index={i}
              elsewhere={p.revision !== null && p.revision !== revision ? revision : null}
              active={p.id === activePin}
              onFocus={() => setActivePin(p.id)}
              onNote={(note) => void act(() => api.updatePin(designId, p.id, { note }))}
              onResolve={() => void act(() => api.updatePin(designId, p.id, { resolved: true }))}
              onRemove={() => void act(() => api.removePin(designId, p.id))}
            />
          ))}
        </ol>
      )}
      {error && <p className="notice fail">{error}</p>}
      {resolved.length > 0 && (
        <details className="resolved-pins">
          <summary>Resolved ({resolved.length})</summary>
          <ul>
            {resolved.map((p) => (
              <li key={p.id}>
                <span>{p.note || "(no note)"}</span>
                {p.reply && <span className="muted"> → {p.reply}</span>}
                <span className="pin-actions">
                  <button type="button" className="link-button" onClick={() => void act(() => api.updatePin(designId, p.id, { resolved: false }))}>
                    Reopen
                  </button>
                  <button type="button" className="link-button" onClick={() => void act(() => api.removePin(designId, p.id))}>
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

interface RowProps {
  pin: Pin;
  index: number;
  /** The revision on screen when the pin is not on it. */
  elsewhere: number | null;
  active: boolean;
  onFocus: () => void;
  onNote: (note: string) => void;
  onResolve: () => void;
  onRemove: () => void;
}

function PinRow({ pin, index, elsewhere, active, onFocus, onNote, onResolve, onRemove }: RowProps) {
  const [draft, setDraft] = useState(pin.note);
  useEffect(() => setDraft(pin.note), [pin.note]);
  const save = () => {
    if (draft.trim() !== pin.note) onNote(draft.trim());
  };
  return (
    <li className={active ? "active" : undefined}>
      <span className="pin-badge" aria-hidden>
        {index + 1}
      </span>
      <div className="pin-body">
        <textarea
          aria-label={`Note for pin ${index + 1}`}
          placeholder="What should change here?"
          rows={2}
          value={draft}
          autoFocus={active && !pin.note}
          onFocus={onFocus}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              (e.target as HTMLTextAreaElement).blur();
            }
          }}
        />
        {pin.label && <span className="muted tiny">{pin.label}</span>}
        {elsewhere !== null && (
          <span className="notice tiny">
            Not found in revision {elsewhere}; this pin is on revision {pin.revision}.
          </span>
        )}
      </div>
      <span className="pin-actions">
        <button type="button" className="link-button" onClick={onResolve} title="Mark as done">
          Resolve
        </button>
        <button type="button" className="icon-button" aria-label={`Remove pin ${index + 1}`} onClick={onRemove}>
          ×
        </button>
      </span>
    </li>
  );
}
