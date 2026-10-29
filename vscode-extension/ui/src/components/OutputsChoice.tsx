import { useState } from "react";
import { api } from "../api/client";
import type { Revision } from "../api/types";
import { useCapabilities } from "../hooks/useCapabilities";

/** What each build writes beside what review needs: design.json `outputs`, chosen here as an agent would write
 * them. A change rebuilds, its parts from the cache; one made on outputs design.json no longer has is refused. */
export function OutputsChoice({ designId, revision }: { designId: string; revision: Revision }) {
  const capabilities = useCapabilities();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shown = revision.spec?.design.outputs ?? [];
  const outputs = capabilities?.outputs ?? [];
  if (!outputs.length) return null;

  const toggle = async (name: string) => {
    const chosen = shown.includes(name) ? shown.filter((n) => n !== name) : [...shown, name];
    setBusy(true);
    setError(null);
    try {
      await api.editOutputs(designId, chosen, shown);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset className="outputs" disabled={busy}>
      <legend className="muted">Written with each build</legend>
      {outputs.map((o) => (
        <label key={o.name} title={o.summary}>
          <input type="checkbox" checked={shown.includes(o.name)} onChange={() => void toggle(o.name)} />{" "}
          {o.title || "Rendered views"} <span className="muted">({o.name})</span>
        </label>
      ))}
      {busy && <p className="hint">Rebuilding…</p>}
      {error && <p className="fail">{error}</p>}
    </fieldset>
  );
}
