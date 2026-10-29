import type { ReactNode } from "react";
import type { BuildProgress, DesignState, Revision } from "../api/types";
import { useCapabilities } from "../hooks/useCapabilities";
import { useDesign } from "../hooks/useDesign";
import { useShown } from "../hooks/useRevision";
import { type DesignUi, type InspectorTab, shownBuild, useDesignUi } from "../lib/designUi";
import { BomPanel } from "./BomPanel";
import { BriefPanel } from "./BriefPanel";
import { ChangesPanel } from "./ChangesPanel";
import { ParametersPanel } from "./ParametersPanel";
import { deliveryStatus } from "../lib/delivery";
import { PinsPanel } from "./PinsPanel";
import { ReportPanel } from "./ReportPanel";
import { StructurePanel } from "./StructurePanel";
import { inspectionTargets } from "../lib/inspection";

/** What a tab renders from: the design, the revision on screen (in full) and the one it is compared with, the build
 * running when it is on screen instead, and the shared UI state. */
export interface TabContext {
  id: string;
  state: DesignState;
  revision: Revision | null;
  building: BuildProgress | null;
  base: Revision | null;
  ui: DesignUi;
  setUi: ReturnType<typeof useDesignUi>[1];
  refresh: () => void;
}

export interface Tab {
  id: InspectorTab;
  label: string;
  badge?: (ctx: TabContext) => number;
  render: (ctx: TabContext) => ReactNode;
}

/** The Design view's tabs, in order. A new panel is one entry here (and its id in InspectorTab). */
export const TABS: Tab[] = [
  {
    id: "pins",
    label: "Pins",
    badge: ({ state }) => state.pins.filter((p) => !p.resolved).length,
    render: ({ id, state, revision, ui, setUi, refresh }) => (
      <PinsPanel
        designId={id}
        pins={state.pins}
        revision={revision?.id ?? null}
        activePin={ui.activePin}
        setActivePin={(pin) => {
          const placed = state.pins.find((p) => p.id === pin);  // a pin placed at a motion's position shows it there
          setUi({ activePin: pin, ...(placed?.motion && placed.revision !== null ? { motion: { ...placed.motion, revision: placed.revision } } : {}) });
        }}
        onPinMode={() => setUi({ placing: true })}
        canPin={Boolean(revision?.report.artifacts.some((a) => a.role === "view"))}
        onChanged={refresh}
        status={deliveryStatus(state)}
      />
    ),
  },
  {
    id: "structure",
    label: "Structure",
    render: ({ id, state, revision, ui, setUi, refresh }) => (
      <StructurePanel
        designId={id}
        revision={revision}
        configuration={ui.configuration}
        latest={state.revisions[state.revisions.length - 1]?.id ?? null}
        onSelected={() => { setUi({ inspection: null }); refresh(); }}
      />
    ),
  },
  {
    id: "parameters",
    label: "Parameters",
    render: ({ state, revision, ui, setUi }) => {
      const instances = inspectionTargets(revision?.id ?? null, ui.inspection, state.selection);
      return <ParametersPanel key={revision?.id ?? "none"} design={state} revision={revision} configuration={ui.configuration}
        instances={instances}
        onFocus={() => {
          if (revision) setUi({ inspection: { revision: revision.id, instances, action: "focus", sequence: (ui.inspection?.sequence ?? 0) + 1 } });
        }}
        setConfiguration={(c) => setUi({ configuration: c, revision: null })} />;
    },
  },
  {
    id: "changes",
    label: "Changes",
    render: ({ state, revision, base, setUi }) => (
      <ChangesPanel revisions={state.revisions} current={revision} base={base} setBaseId={(rev) => setUi({ base: rev })} />
    ),
  },
  {
    id: "report",
    label: "Results",
    badge: ({ revision, building }) => {
      const tally = building?.tally ?? revision?.report.tally;
      return tally ? tally.problems + tally.warnings : 0;
    },
    render: ({ id, revision, building, ui, setUi }) => <ReportPanel designId={id} revision={revision} building={building} onInspect={(instances) => setUi({
      placing: false, motion: null,
      inspection: { revision: revision!.id, instances, action: "focus", sequence: (ui.inspection?.sequence ?? 0) + 1 },
    })} />,
  },
  { id: "bom", label: "BOM", render: ({ revision }) => <BomPanel revision={revision} /> },
  { id: "brief", label: "Brief", render: ({ state }) => <BriefPanel brief={state.brief} /> },
];

/** A design's pins, structure, parameters, changes, results, bill of materials and brief: VS Code's "hitlcad: Design"
 * view, beside the 3D editor. */
export function InspectorView({ id }: { id: string }) {
  const live = useDesign(id);
  const { state } = live;
  const [ui, setUi] = useDesignUi();
  const { revision, base } = useShown(id, state?.revisions ?? [], ui);
  const problems = useCapabilities()?.plugin_problems ?? [];

  if (!state) {
    return (
      <main className="design-empty">
        {live.error ? <p className="notice fail">{live.error}</p> : <p className="muted">Opening design…</p>}
      </main>
    );
  }

  const ctx: TabContext = { id, state, revision, building: shownBuild(state.building, ui), base, ui, setUi, refresh: live.refresh };
  const active = TABS.find((t) => t.id === ui.tab) ?? TABS[0]!;

  return (
    <div className="inspector">
      {problems.map((p, k) => (
        <p key={k} className="notice fail" title={p.code}>
          {p.message}
        </p>
      ))}
      <div className="tabs" role="tablist">
        {TABS.map((t) => {
          const count = t.badge?.(ctx) ?? 0;
          return (
            <button key={t.id} type="button" role="tab" aria-selected={t === active} className={t === active ? "tab on" : "tab"} onClick={() => setUi({ tab: t.id })}>
              {t.label}
              {count > 0 && <span className="tab-count">{count}</span>}
            </button>
          );
        })}
      </div>
      <div className="tab-body" role="tabpanel">
        {active.render(ctx)}
      </div>
    </div>
  );
}
