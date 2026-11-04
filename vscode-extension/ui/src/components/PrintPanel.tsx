import { type FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import type { BuildReport, PlannedPart, Printer, PrinterSetup, PrintOrientation, Revision } from "../api/types";
import type { DesignUi } from "../lib/designUi";
import type { Inspection } from "../lib/inspection";
import { toExtension } from "../lib/host";
import { SHADES } from "../lib/shading";

/** The printed part a print focus (`DesignUi.printFocus`) chose, while it is in the plan. */
export function chosenPart(report: BuildReport, focus: Inspection | null): PlannedPart | null {
  return focus?.action === "focus" ? plannedAt(report, focus.instances[0] ?? "") : null;
}

/** Choose a printed part (by its path in the plan), or none: the print view frames it, the Print tab shows its plan. */
export function choosePrinted(ui: DesignUi, revision: number, path: string | null): Partial<DesignUi> {
  return { printFocus: { revision, instances: path ? [path] : [], action: path ? "focus" : "restore", sequence: (ui.printFocus?.sequence ?? 0) + 1 } };
}

/**
 * Preparing the design for printing: the human's printer (asked for once, kept in the project's hitlcad.toml), then
 * the print plan of the revision on screen, the chosen part's in full.
 */
export function PrintPanel({ designId, revision, chosen, onChoose, onPoint }: {
  designId: string; revision: Revision | null; chosen: PlannedPart | null; onChoose: (path: string | null) => void;
  /** A way up pointed at in a part's plan (null: none), which the print view shows the part lying. */
  onPoint: (path: string, up: PrintOrientation["up"] | null) => void;
}) {
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
      <div className="print">
        <PrinterForm setup={setup} onCancel={setup.printer ? () => setEditing(false) : null} onSave={async (printer) => {
          setSetup(await api.setPrinter(designId, printer));
          setEditing(false);
        }} />
      </div>
    );
  }
  const printer = setup.printer;
  const slicer = setup.slicers.find((s) => s.name === printer.slicer)?.title ?? printer.slicer;
  const plan = revision?.report.print_plan ?? [];
  const pieces = plan.reduce((n, p) => n + p.count, 0);
  return (
    <div className="print">
      <div className="printer-summary">
        <p>
          <b>{printer.name}</b>: {printer.build_volume_mm.join(" × ")} mm, {printer.nozzle_mm} mm nozzle, sliced in {slicer}.
        </p>
        <button type="button" className="button small" onClick={() => setEditing(true)}>Edit printer</button>
      </div>
      {revision && plan.length > 0 && <SlicerFile designId={designId} revision={revision} slicer={slicer} />}
      {!revision ? <p className="muted">The print plan appears after the first build.</p>
        : !plan.length ? <p className="muted">No part of revision {revision.id} is printed: give a part an FDM or resin process.</p>
        : chosen ? (
          <>
            <ShadeKey />
            <button type="button" className="link-button plan-back" onClick={() => onChoose(null)}>‹ All {plan.length} printed parts</button>
            <PlanItem key={chosen.path} designId={designId} revision={revision} part={chosen} onPoint={(up) => onPoint(chosen.path, up)} />
          </>
        ) : (
          <>
            <ShadeKey />
            <p className="muted tiny">{pieces} piece{pieces === 1 ? "" : "s"} of {plan.length} part{plan.length === 1 ? "" : "s"}. Click one, here or in the print view, for its plan.</p>
            <ol className="print-plan">
              {plan.map((p) => {
                const now = current(p);
                return (
                  <li key={p.path}>
                    <button type="button" className="plan-row" onClick={() => onChoose(p.path)}>
                      <b>{p.path}</b> ×{p.count} · {p.material ?? "no material"} · {now.label}
                      {now.fits === false && <span className="print-fail"> · does not fit</span>}
                    </button>
                  </li>
                );
              })}
            </ol>
          </>
        )}
    </div>
  );
}

/** The file to print from, as the build wrote it for the printer's slicer (the `print` output), and the page that
 * says how to open it there; or which output writes them. */
function SlicerFile({ designId, revision, slicer }: { designId: string; revision: Revision; slicer: string }) {
  const files = revision.report.artifacts.filter((a) => a.role === "print");
  if (!files.length) {
    return <p className="muted tiny">For a file to open in {slicer}, every part lying as it prints with its settings, choose <b>For the slicer</b> among the build's outputs (Results, under Files).</p>;
  }
  return (
    <div className="slicer-file">
      <p className="tiny">
        Open {files.map((a, k) => (
          <span key={a.path}>
            {k > 0 && " and "}
            <button type="button" className="link-button" title={`Save a copy of ${a.name}`}
              onClick={() => toExtension({ type: "hitlcad.saveFile", design: designId, revision: revision.id, path: a.path, name: a.name })}>
              {a.path}
            </button>
          </span>
        ))} from the design's <code>build/</code> folder: open the 3MF in {slicer} as a project, then arrange and slice; the page says how, and what each part is set to.
      </p>
    </div>
  );
}

/** What the print view's shading means, as its process checks found it. */
function ShadeKey() {
  return (
    <p className="shade-key muted tiny">
      In the print view, shaded through the part:
      {Object.values(SHADES).map(({ color, label }) => <span key={label}><span className="shade-swatch" style={{ background: color }} />{label}</span>)}
    </p>
  );
}

/** The way a planned part prints now. */
const current = (p: PlannedPart): PrintOrientation => p.ways.find((w) => w.current)!;

/** The planned part a path of the print view is of (a part of several colours is a group under its own path). */
export function plannedAt(report: BuildReport, path: string): PlannedPart | null {
  return report.print_plan.filter((p) => path === p.path || path.startsWith(`${p.path}/`))
    .sort((a, b) => b.path.length - a.path.length)[0] ?? null;
}

/** One way a planned part can lie, as its process judges it: whether it fits and stays put, the support it needs, what
 * lies on the bed, its height, and its fitted round features it would leave out of round. */
function wayText(w: PrintOrientation): string {
  const fits = w.fits === null ? "" : w.fits ? " · fits" : " · does not fit";
  const stands = w.stands ? "" : " · does not stay put";
  const round = w.harmed.length ? ` · ${w.harmed.join(", ")}` : "";
  const shows = (mm2: number) => (Math.round(mm2) ? ` (${mm2.toFixed(0)} shows)` : "");
  return `${w.label}: ${w.support_mm2.toFixed(0)} mm² support${shows(w.shown_support_mm2)}, ${w.bed_mm2.toFixed(0)} mm² on the bed${shows(w.shown_bed_mm2)}, ${w.height_mm.toFixed(0)} mm tall${fits}${stands}${round}`;
}

/** The planned parts that do not fit the printer's bed as they lie: what the Print tab's badge counts. */
export function unfit(report: BuildReport): number {
  return report.print_plan.filter((p) => current(p).fits === false).length;
}

/** What the print view's hover calls a printed part: its path, how many, and which way up; its plan is a click away. */
export function printNames(report: BuildReport): (path: string) => string {
  return (path) => {
    const p = plannedAt(report, path);
    if (!p) return path;
    const now = current(p);
    return `${p.path} ×${p.count} · ${now.label}${now.fits === false ? " · does not fit" : ""}`;
  };
}

/**
 * One printed part's plan: how many, its material, which way up and whether it fits the bed; its settings, each
 * recommended or decided by the agent, with the reason; and the ways it can lie, best first, each one click from
 * printing that way (a panel edit of its `up`, no agent turn). A component's parts are its own design's to turn.
 */
function PlanItem({ designId, revision, part, onPoint }: {
  designId: string; revision: Revision; part: PlannedPart; onPoint: (up: PrintOrientation["up"] | null) => void;
}) {
  const [turning, setTurning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = current(part);
  const turn = (up: PrintOrientation["up"]) => {
    setTurning(true);
    setError(null);
    api.turn(designId, part.path, up, revision.version).catch((e: Error) => setError(e.message)).finally(() => setTurning(false));
  };
  return (
    <article className="plan-item">
      <h3 className="plan-head">
        <b>{part.path}</b> ×{part.count} · {part.material ?? "no material"} · {now.label}
        {now.fits !== null && <span className={now.fits ? "print-pass" : "print-fail"}> · {now.fits ? "fits the bed" : "does not fit the bed"}</span>}
      </h3>
      <ul className="plan-settings">
        {part.settings.map((s) => (
          <li key={s.name}>
            <span className={s.decided ? "print-decided" : "print-recommended"}>{s.text}</span>
            <span className="muted"> {s.decided ? "decided" : "recommended"}: {s.why}</span>
          </li>
        ))}
      </ul>
      {part.ways.length > 1 && (
        <section className="plan-ways">
          <h4>{part.ways.some((w) => w.better) ? "It could lie better." : "It prints the best way up."} The ways it can lie, best first:</h4>
          <ol>
            {part.ways.map((w, n) => (
              <li key={w.up.join()} className={w.current ? "way-current" : ""} tabIndex={0}
                onMouseEnter={() => onPoint(w.up)} onMouseLeave={() => onPoint(null)} onFocus={() => onPoint(w.up)} onBlur={() => onPoint(null)}>
                {n === 0 && <b>best </b>}{wayText(w)}
                {w.current ? <span className="muted"> (as it prints now)</span> : !part.path.includes("/") && (
                  <button type="button" className="button small" disabled={turning} onClick={() => turn(w.up)}>Print this way</button>
                )}
                {w.problems.length > 0 && (
                  <details className="way-problems"><summary className="muted tiny">what its checks find</summary>
                    <ul>{w.problems.map((p) => <li key={p} className="muted tiny">{p}</li>)}</ul>
                  </details>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
      {turning && <p className="muted tiny">Turning {part.path} and building…</p>}
      {error && <p className="notice fail">{error}</p>}
    </article>
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
