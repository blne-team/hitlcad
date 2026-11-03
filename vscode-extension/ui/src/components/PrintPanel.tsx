import { type FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import type { BuildReport, PlannedPart, Printer, PrinterSetup, PrintOrientation, Revision } from "../api/types";

/**
 * Preparing the design for printing: the human's printer (asked for once, kept in the project's hitlcad.toml), then
 * the print plan of the revision on screen.
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
      {revision ? <PlanList designId={designId} revision={revision} /> : <p className="muted pad">The print plan appears after the first build.</p>}
    </div>
  );
}

/** The way a planned part prints now. */
const current = (p: PlannedPart): PrintOrientation => p.ways.find((w) => w.current)!;

/** One way a planned part can lie, as a person weighs it: the support it needs, what lies on the bed, its height. */
function wayText(w: PrintOrientation): string {
  const fits = w.fits === null ? "" : w.fits ? " · fits" : " · does not fit";
  return `${w.label}: ${w.overhang_mm2.toFixed(0)} mm² support, ${w.bed_mm2.toFixed(0)} mm² on the bed, ${w.height_mm.toFixed(0)} mm tall${fits}`;
}

/** The planned parts that do not fit the printer's bed as they lie: what the Print tab's badge counts. */
export function unfit(report: BuildReport): number {
  return report.print_plan.filter((p) => current(p).fits === false).length;
}

/** What the print view's hover names a printed part with: its path, how many, which way up, and its settings. */
export function printNames(report: BuildReport): (path: string) => string {
  const by = new Map(report.print_plan.map((p) => [p.path, p]));
  return (path) => {
    const p = by.get(path);
    return p ? `${p.path} ×${p.count} · ${current(p).label} · ${p.settings.map((s) => s.text).join(", ")}` : path;
  };
}

/**
 * A revision's print plan (`BuildReport.print_plan`): each printed part with how many, its material, which way up and
 * whether it fits the bed, its settings (recommended, or decided by the agent, each with its reason), and the other
 * ways it could lie, best first, each one click from printing that way: a panel edit of its `up`, no agent turn. A
 * component's parts are its own design's to turn.
 */
function PlanList({ designId, revision }: { designId: string; revision: Revision }) {
  const [turning, setTurning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const plan = revision.report.print_plan;
  if (!plan.length) return <p className="muted pad">No part of revision {revision.id} is printed: give a part an FDM or resin process.</p>;
  const turn = (part: string, up: PrintOrientation["up"]) => {
    setTurning(part);
    setError(null);
    api.turn(designId, part, up, revision.version).catch((e: Error) => setError(e.message)).finally(() => setTurning(null));
  };
  return (
    <>
      <ol className="print-plan">
        {plan.map((p) => {
          const now = current(p);
          const best = p.ways[0]!;
          return (
            <li key={p.path}>
              <div className="plan-head">
                <b>{p.path}</b> ×{p.count} · {p.material ?? "no material"} · {now.label}
                {now.fits !== null && <span className={now.fits ? "print-pass" : "print-fail"}> · {now.fits ? "fits the bed" : "does not fit the bed"}</span>}
              </div>
              <div className="plan-settings">
                {p.settings.map((s) => (
                  <span key={s.name} className={s.decided ? "print-decided" : "print-recommended"} title={`${s.decided ? "Decided" : "Recommended"}: ${s.why}`}>
                    {s.text}
                  </span>
                ))}
              </div>
              {p.ways.length > 1 && (
                <details className="plan-ways" open={!best.current}>
                  <summary>{best.current ? "It prints the best way up" : `Best: ${wayText(best)}`}</summary>
                  <ol>
                    {p.ways.map((w, n) => (
                      <li key={w.up.join()} className={w.current ? "way-current" : ""}>
                        {n === 0 && <b>best </b>}{wayText(w)}
                        {w.current ? <span className="muted"> (as it prints now)</span> : !p.path.includes("/") && (
                          <button type="button" className="button small" disabled={turning !== null} onClick={() => turn(p.path, w.up)}>Print this way</button>
                        )}
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </li>
          );
        })}
      </ol>
      {turning && <p className="muted tiny">Turning {turning} and building…</p>}
      {error && <p className="notice fail">{error}</p>}
    </>
  );
}

/** Beside the print view: the revision's print plan. */
export function PrintPlanBox({ designId, revision }: { designId: string; revision: Revision }) {
  const pieces = revision.report.print_plan.reduce((n, p) => n + p.count, 0);
  return (
    <section className="selection-box print-plan-box" aria-label="Print plan">
      <header>
        <span className="sel-title">Print plan</span>
        <span className="muted tiny">{pieces} piece{pieces === 1 ? "" : "s"} of {revision.report.print_plan.length} part{revision.report.print_plan.length === 1 ? "" : "s"}</span>
      </header>
      <PlanList designId={designId} revision={revision} />
    </section>
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
