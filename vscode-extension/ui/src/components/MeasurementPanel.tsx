import { useState } from "react";
import { api } from "../api/client";
import type { Pick, SelectionItem } from "../api/types";

interface Props {
  designId: string;
  revision: number;
  items: [SelectionItem, SelectionItem];
  onPin: (pick: Pick, note: string) => Promise<void>;
}

/** Measurements are made on the built geometry and saved as revision-labelled evidence in a pin. */
export function MeasurementPanel({ designId, revision, items, onPin }: Props) {
  const [text, setText] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const measure = () => {
    setBusy(true);
    setError(null);
    api.measure(designId, revision, items).then(
      (result) => { setText(result.text); setBusy(false); },
      (e: Error) => { setError(e.message); setBusy(false); },
    );
  };
  const save = () => {
    setBusy(true);
    setError(null);
    const { point, normal, component } = items[0];
    onPin({ point, normal, component }, `Measured in revision ${revision}:\n${text}\n\n${note.trim()}`).then(
      () => { setSaved(true); setBusy(false); },
      (e: Error) => { setError(e.message); setBusy(false); },
    );
  };

  return (
    <div className="measurement-panel">
      <button type="button" className="button small" disabled={busy} onClick={measure}>Measure selected pair</button>
      {!items[0].component || !items[1].component ? <p className="muted tiny">Points measure where you clicked.</p> : null}
      {text && <>
        <p className="measurement-result">{text}</p>
        <textarea aria-label="Measurement note" placeholder="What should change?" value={note} onChange={(e) => { setNote(e.target.value); setSaved(false); }} />
        <button type="button" className="button small" disabled={busy || saved} onClick={save}>{saved ? "Saved to pin" : "Pin measurement"}</button>
      </>}
      {error && <p className="notice fail">{error}</p>}
    </div>
  );
}
