import { useState } from "react";
import { api } from "../api/client";
import type { DesignState, ParamSpec, ParamValue, Revision } from "../api/types";
import { humanize } from "../lib/format";
import { parametersForInstances } from "../lib/parameters";

interface Props {
  design: DesignState;
  revision: Revision | null;
  configuration: string | null;
  setConfiguration: (name: string | null) => void;
  instances: string[];
  onFocus: () => void;
}

/** The configurations of the design on screen, and its parameters (the default configuration) to tune. Keyed by
 * the revision: what was typed is a draft over the revision on screen, kept while the design's state refreshes. */
export function ParametersPanel({ design, revision, configuration, setConfiguration, instances, onFocus }: Props) {
  const specs = revision?.spec?.design.parameters ?? [];
  const configurations = revision?.spec?.design.configurations ?? {};
  const original = Object.fromEntries(specs.map((p) => [p.name, p.value]));
  // null: a number field left empty, which holds back rebuilding until it has a value again
  const [values, setValues] = useState<Record<string, ParamValue | null>>(original);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<"selection" | "all">("selection");


  if (!revision) return <p className="muted pad">Parameters appear after the first build.</p>;
  if (specs.length === 0) return <p className="muted pad">This revision has no tunable parameters.</p>;

  const changed = Object.fromEntries(Object.entries(values).filter((e): e is [string, ParamValue] => e[1] !== null && original[e[0]] !== e[1]));
  const empty = Object.values(values).includes(null);
  const dirty = Object.keys(changed).length > 0 || empty;
  const working = design.building !== null;
  const related = parametersForInstances(revision, instances);
  const scoped = instances.length > 0 && scope === "selection";
  const visible = scoped ? specs.filter((p) => related.has(p.name)) : specs;
  const hiddenEdits = scoped ? specs.filter((p) => !related.has(p.name) && (p.name in changed || values[p.name] === null)).length : 0;

  const rebuild = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.editParameters(design.id, changed, Object.fromEntries(Object.keys(changed).map((k) => [k, original[k]!])));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const show = async (name: string | null) => {
    setConfiguration(name);
    if (design.revisions.some((r) => r.configuration === name)) return;
    try {
      await api.build(design.id, name); // a configuration never built: build it
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="params">
      <section className={`parameter-context${instances.length ? " selected" : ""}`} aria-label="Parameter context">
        <div className="parameter-context-head">
          <span className="muted tiny">{instances.length ? "Selected parts" : "Whole design"}</span>
          <span className="muted tiny">Revision {revision.id}</span>
        </div>
        <div className="parameter-targets" aria-live="polite">
          {instances.length ? instances.map((instance) => <strong key={instance} title={instance}>{instance}</strong>) : <strong>{design.name}</strong>}
        </div>
        {instances.length ? <>
          <div className="parameter-context-actions">
            <div className="parameter-scope" role="group" aria-label="Parameter scope">
              <button type="button" className={`toggle${scoped ? " on" : ""}`} aria-pressed={scoped} onClick={() => setScope("selection")}>For selection</button>
              <button type="button" className={`toggle${!scoped ? " on" : ""}`} aria-pressed={!scoped} onClick={() => setScope("all")}>All parameters</button>
            </div>
            <button type="button" className="link-button" onClick={onFocus} disabled={working}>Locate in model</button>
          </div>
          <p className="hint">{scoped ? "Inputs used to build or place these parts. " : "All design inputs. "}Shared inputs may also change other parts.</p>
        </> : <p className="hint">Select a part in the model to see its related parameters.</p>}
      </section>
      {Object.keys(configurations).length > 0 && (
        <div className="configurations" role="radiogroup" aria-label="Configuration">
          {[null, ...Object.keys(configurations)].map((name) => (
            <button key={name ?? ""} type="button" role="radio" aria-checked={configuration === name}
              className={`toggle${configuration === name ? " on" : ""}`} disabled={working} onClick={() => void show(name)}
              title={name ? Object.entries(configurations[name]!).map(([k, v]) => `${k} = ${String(v)}`).join(", ") : "The parameters as written"}>
              {name ?? "default"}
            </button>
          ))}
        </div>
      )}
      <p className="hint">
        {configuration ? `Editing default values; configuration ${configuration} overrides ${Object.keys(configurations[configuration] ?? {}).join(", ")}. ` : ""}
        Rebuild saves all edits to the design.
      </p>
      {scoped && !visible.length && <p className="muted">No related tunable parameters were recorded for this selection. View all parameters, or open the component’s own design to edit its local inputs.</p>}
      {visible.map((p) => (
        <ParamRow key={p.name} spec={p} value={values[p.name] as ParamValue | null} changed={p.name in changed}
          onChange={(v) => setValues((all) => ({ ...all, [p.name]: v }))} />
      ))}
      {error && <p className="notice fail">{error}</p>}
      {hiddenEdits > 0 && <p className="hint" role="status">{hiddenEdits} edited parameter{hiddenEdits === 1 ? " is" : "s are"} outside this selection. Rebuild applies all edits. View all parameters to review them.</p>}
      <div className="actions sticky-actions">
        <button type="button" className="button primary" disabled={!dirty || empty || busy || working} onClick={() => void rebuild()}>
          {busy ? "Rebuilding…" : "Rebuild with these values"}
        </button>
        <button type="button" className="button" disabled={!dirty || busy} onClick={() => setValues(original)}>
          Reset
        </button>
      </div>
      {working && !busy && <p className="hint">Rebuilding is available once the current build finishes.</p>}
    </div>
  );
}


/** The parameter's own step, else 1 for integers, else the power of ten that gives 100–1000 stops: round values in mm. */
function sliderStep(spec: ParamSpec, min: number, max: number): number {
  return spec.step ?? (spec.type === "int" ? 1 : 10 ** Math.floor(Math.log10((max - min) / 100)));
}

function ParamRow({ spec, value, changed, onChange }: { spec: ParamSpec; value: ParamValue | null; changed: boolean; onChange: (v: ParamValue | null) => void }) {
  const label = spec.label || humanize(spec.name);
  const id = `param-${spec.name}`;
  return (
    <div className={`param${changed ? " changed" : ""}`}>
      <div className="param-head">
        <label htmlFor={id} title={spec.name}>{label}</label>
        {spec.type !== "bool" && spec.type !== "choice" && <span className="unit">{spec.unit}</span>}
      </div>
      {spec.type === "bool" ? (
        <label className="check">
          <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
          <span>{value ? "On" : "Off"}</span>
        </label>
      ) : spec.type === "choice" ? (
        <select id={id} value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {(spec.choices ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      ) : (
        <div className="param-inputs">
          {spec.min !== null && spec.max !== null && (
            <input type="range" aria-label={`${label} slider`} min={spec.min} max={spec.max} step={sliderStep(spec, spec.min, spec.max)}
              value={typeof value === "number" ? value : spec.min}
              onChange={(e) => onChange(Number(e.target.valueAsNumber.toFixed(10)))} />
          )}
          <input id={id} className="num-input" type="number" min={spec.min ?? undefined} max={spec.max ?? undefined}
            step={spec.step ?? (spec.type === "int" ? 1 : "any")} value={typeof value === "number" ? value : ""}
            onChange={(e) => onChange(Number.isNaN(e.target.valueAsNumber) ? null : e.target.valueAsNumber)} />
        </div>
      )}
      {spec.description && <span className="hint">{spec.description}</span>}
    </div>
  );
}
