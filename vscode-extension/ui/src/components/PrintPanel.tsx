import { type FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import type { BuildReport, PartResult, Printer, PrinterSetup, PrintSetting, Result, Revision } from "../api/types";

/** A print setting as a person reads it: "0.2 mm layers", "4 walls", "15 % infill", "supports", "no brim". */
export function settingText({ name, value }: PrintSetting): string {
  if (name === "layer_mm") return `${value} mm layers`;
  if (name === "walls") return `${value} walls`;
  if (name === "infill_percent") return `${value} % infill`;
  return value ? name : `no ${name}`;
}

/** Which way up a part prints, as a person reads it: "as modelled", "on its +Y face", or the direction. */
export function upText(up: [number, number, number]): string {
  const [x, y, z] = up;
  if (x === 0 && y === 0 && z > 0) return "as modelled";
  const axis = [x, y, z].map(Math.abs);
  const k = axis.indexOf(Math.max(...axis));
  if (axis.filter((v) => v > 0).length === 1) return `on its ${up[k]! > 0 ? "-" : "+"}${"XYZ"[k]} face`;
  return `up [${up.map((v) => +v.toFixed(3)).join(", ")}]`;
}

/**
 * Preparing the design for printing: the human's printer (asked for once, kept in the project's hitlcad.toml), then
 * each printed part of the revision on screen with which way up it prints, whether it fits the bed, and its process
 * findings.
 */
export function PrintPanel({ designId, revision }: { designId: string; revision: Revision | null }) {
  const [setup, setSetup] = useState<PrinterSetup | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.printer(designId).then(setSetup, (e: Error) => setError(e.message));
  }, [designId, revision?.id]);

  if (error) return <p className="notice fail">{error}</p>;
  if (!setup) return <p className="muted pad">Reading the project's printer…</p>;
  if (!setup.file) {
    return <p className="muted pad">This design is in no project, which keeps the printer: make one with <b>hitlcad: New Design Project</b> (or <code>hitlcad init</code>).</p>;
  }
  if (!setup.printer || editing) {
    return (
      <PrinterForm setup={setup} onCancel={setup.printer ? () => setEditing(false) : null} onSave={async (printer) => {
        setSetup(await api.setPrinter(designId, printer));
        setEditing(false);
      }} />
    );
  }
  const printer = setup.printer;
  const slicer = setup.slicers.find((s) => s.name === printer.slicer)?.title ?? printer.slicer;
  return (
    <div className="print">
      <div className="printer-summary">
        <p>
          <b>{printer.name}</b>: {printer.build_volume_mm.join(" × ")} mm, {printer.nozzle_mm} mm nozzle, sliced in {slicer}.
        </p>
        <button type="button" className="button small" onClick={() => setEditing(true)}>Edit printer</button>
      </div>
      <PrintedParts revision={revision} />
    </div>
  );
}

/** A printed part of a build, its own or a component's, with how many of it the whole design places. */
export interface Printed {
  name: string;
  part: PartResult;
  count: number;
  /** The report it is in: the design's, or its component's, whose results are about it. */
  report: BuildReport;
}

/** Every printed part the design places, through its components, each once with its count. */
export function printed(report: BuildReport): Printed[] {
  const found = new Map<string, Printed>();
  const walk = (r: BuildReport, prefix: string) => {
    for (const instance of r.instances) {
      if (instance.component) walk(r.components[instance.component]!, `${prefix}${instance.component}/`);
      const part = instance.part ? r.parts.find((p) => p.name === instance.part) : undefined;
      if (!part?.up) continue;
      const name = prefix + part.name;
      found.set(name, { name, part, report: r, count: (found.get(name)?.count ?? 0) + 1 });
    }
  };
  walk(report, "");
  return [...found.values()];
}

/** The printed parts that do not fit the printer's bed: what the Print tab's badge counts. */
export function unfit(report: BuildReport): number {
  return printed(report).filter(({ part, report: r }) =>
    r.results.some((x) => x.analysis === "printing" && x.status === "fail" && x.about.includes(part.name))).length;
}

function PrintedParts({ revision }: { revision: Revision | null }) {
  if (!revision) return <p className="muted pad">The printed parts appear after the first build.</p>;
  const parts = printed(revision.report);
  if (!parts.length) return <p className="muted pad">No part of revision {revision.id} is printed.</p>;
  const of = ({ part, report }: Printed, analysis: string) =>
    report.results.filter((r) => r.analysis === analysis && r.about.includes(part.name));
  const mark = (r: Result | undefined) => (r ? <span className={`print-${r.status}`} title={r.detail}>{r.status === "pass" ? "fits" : r.status === "fail" ? "does not fit" : "unchecked"}</span> : <span className="muted">no printer</span>);
  const pieces = parts.reduce((n, p) => n + p.count, 0);
  return (
    <>
      <p className="muted">
        {pieces} printed piece{pieces === 1 ? "" : "s"} of {parts.length} part{parts.length === 1 ? "" : "s"} in revision {revision.id}.
        Each part's settings are below it: <span className="print-recommended">recommended</span> by hitlcad from what the build
        found, or decided by your agent in the part's manufacturing; hover one for its reason.
      </p>
      <table className="metrics print-parts">
        <thead>
          <tr>
            <th>Part</th>
            <th>Qty</th>
            <th>Material</th>
            <th>Prints</th>
            <th>Bed</th>
            <th>Process</th>
          </tr>
        </thead>
        <tbody>
          {parts.map((p) => {
            const failing = of(p, "manufacturing").filter((r) => r.status === "fail");
            return [
              <tr key={p.name}>
                <th>{p.name}</th>
                <td>{p.count}</td>
                <td>{p.part.material ?? "—"}</td>
                <td>{upText(p.part.up!)}</td>
                <td>{mark(of(p, "printing")[0])}</td>
                <td title={failing.map((r) => r.detail).join("\n")}>{failing.length ? <span className="print-fail">{failing.length} to fix</span> : <span className="print-pass">ok</span>}</td>
              </tr>,
              <tr key={`${p.name} settings`} className="print-settings">
                <td colSpan={6}>
                  {p.part.print.map((s) => (
                    <span key={s.name} className={s.decided ? "print-decided" : "print-recommended"} title={`${s.decided ? "Decided" : "Recommended"}: ${s.why}`}>
                      {settingText(s)}
                    </span>
                  ))}
                </td>
              </tr>,
            ];
          })}
        </tbody>
      </table>
    </>
  );
}

/** The human's printer, asked for once per project: its bed, nozzle, slicer, and a filament for each printed material. */
function PrinterForm({ setup, onSave, onCancel }: { setup: PrinterSetup; onSave: (printer: Printer) => Promise<void>; onCancel: (() => void) | null }) {
  const now = setup.printer;
  const [printer, setPrinter] = useState<Printer>(now ?? {
    name: "", build_volume_mm: [256, 256, 256], nozzle_mm: 0.4, slicer: setup.slicers[0]?.name ?? "", materials: {},
  });
  const [error, setError] = useState<string | null>(null);
  const materials = [...new Set([...setup.materials, ...Object.keys(printer.materials ?? {})])].sort();
  const set = (patch: Partial<Printer>) => setPrinter((p) => ({ ...p, ...patch }));
  const filament = (material: string, patch: { profile?: string; slot?: number | null }) => set({
    materials: { ...printer.materials, [material]: { profile: "", ...printer.materials?.[material], ...patch } },
  });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    // a material left without a filament profile is not kept
    const kept = Object.fromEntries(Object.entries(printer.materials ?? {}).filter(([, f]) => f.profile.trim()));
    await onSave({ ...printer, materials: kept }).catch((err: Error) => setError(err.message));
  };
  return (
    <form className="printer-form" onSubmit={submit}>
      <p className="muted">{now ? "Your printer, kept in this project's hitlcad.toml." : "Which printer will you print this on? hitlcad keeps it in this project's hitlcad.toml, and checks every printed part against it."}</p>
      <label>Printer<input required value={printer.name} placeholder="Bambu Lab P2S" onChange={(e) => set({ name: e.target.value })} /></label>
      <fieldset>
        <legend>Build volume, mm</legend>
        {(["X", "Y", "Z"] as const).map((axis, k) => (
          <label key={axis}>{axis}<input className="num-input" type="number" required min={1} step="any" value={printer.build_volume_mm[k]}
            onChange={(e) => set({ build_volume_mm: printer.build_volume_mm.map((v, i) => (i === k ? Number(e.target.value) : v)) as Printer["build_volume_mm"] })} /></label>
        ))}
      </fieldset>
      <label>Nozzle, mm<input className="num-input" type="number" required min={0.05} step="any" value={printer.nozzle_mm} onChange={(e) => set({ nozzle_mm: Number(e.target.value) })} /></label>
      <label>Slicer
        <select value={printer.slicer} onChange={(e) => set({ slicer: e.target.value })}>
          {setup.slicers.map((s) => <option key={s.name} value={s.name}>{s.title}</option>)}
        </select>
      </label>
      {materials.length > 0 && (
        <fieldset>
          <legend>Filament for each material, and its AMS slot</legend>
          {materials.map((m) => (
            <div className="filament" key={m}>
              <span>{m}</span>
              <input aria-label={`${m} filament profile`} placeholder="Bambu PLA Basic" value={printer.materials?.[m]?.profile ?? ""} onChange={(e) => filament(m, { profile: e.target.value })} />
              <input aria-label={`${m} AMS slot`} className="num-input" type="number" min={1} placeholder="slot" value={printer.materials?.[m]?.slot ?? ""}
                onChange={(e) => filament(m, { slot: e.target.value ? Number(e.target.value) : null })} />
            </div>
          ))}
        </fieldset>
      )}
      {error && <p className="notice fail">{error}</p>}
      <div className="actions">
        <button type="submit" className="button primary">Save printer</button>
        {onCancel && <button type="button" className="button" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

/** What the print view's hover names a printed part with: its path, how many, which way up, and its settings. */
export function printNames(report: BuildReport): (path: string) => string {
  const by = new Map(printed(report).map((p) => [p.name, p]));
  return (path) => {
    const p = by.get(path);
    return p ? `${p.name} ×${p.count} · ${upText(p.part.up!)} · ${p.part.print.map(settingText).join(", ")}` : path;
  };
}

/** Beside the print view: each printed part's plan, its count, which way up, whether it fits the bed, its settings. */
export function PrintPlanBox({ revision }: { revision: Revision }) {
  const parts = printed(revision.report);
  const pieces = parts.reduce((n, p) => n + p.count, 0);
  return (
    <section className="selection-box print-plan-box" aria-label="Print plan">
      <header>
        <span className="sel-title">Print plan</span>
        <span className="muted tiny">{pieces} piece{pieces === 1 ? "" : "s"} of {parts.length} part{parts.length === 1 ? "" : "s"}</span>
      </header>
      <ol>
        {parts.map((p) => {
          const bed = p.report.results.find((r) => r.analysis === "printing" && r.about.includes(p.part.name));
          return (
            <li key={p.name}>
              <div className="plan-head">
                <b>{p.name}</b> ×{p.count} · {p.part.material ?? "no material"} · {upText(p.part.up!)}
                {bed && <span className={`print-${bed.status}`} title={bed.detail}> · {bed.status === "pass" ? "fits the bed" : "does not fit the bed"}</span>}
              </div>
              <div className="plan-settings">
                {p.part.print.map((s) => (
                  <span key={s.name} className={s.decided ? "print-decided" : "print-recommended"} title={`${s.decided ? "Decided" : "Recommended"}: ${s.why}`}>
                    {settingText(s)}
                  </span>
                ))}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
