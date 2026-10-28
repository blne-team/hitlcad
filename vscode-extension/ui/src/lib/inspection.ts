import type { BuildReport, Revision, SelectionItem } from "../api/types";

export type InspectionAction = "focus" | "isolate" | "sectionX" | "sectionY" | "sectionZ" | "restore";

/** A viewer action, shared between the Results panel and the 3D editor. */
export interface Inspection {
  revision: number | null;
  instances: string[];
  action: InspectionAction;
  sequence: number;
}

/** Resolve result addresses to the leaves drawn by the viewer, including repeated parts and components. */
export function affectedInstances(revision: Revision, addresses: string[]): string[] {
  const owners = addresses.map((a) => a.split(".")[0]!);
  const found: string[] = [];
  const walk = (report: BuildReport, prefix: string) => {
    for (const instance of report.instances) {
      const path = prefix + instance.path;
      if (instance.component) walk(report.components[instance.component]!, `${path}/`);
      else if (owners.some((a) => a === prefix + instance.part || a === path || path.startsWith(`${a}/`) || path.startsWith(`${a}[`))) found.push(path);
    }
  };
  walk(revision.report, "");
  return found;
}

export function selectedInstances(items: SelectionItem[]): string[] {
  return [...new Set(items.flatMap((i) => i.instance ?? i.component?.instance ?? []))];
}

/** The same editing context in both webviews; old revisions never supply a target. */
export function inspectionTargets(revision: number | null, inspection: Inspection | null, items: SelectionItem[]): string[] {
  if (revision === null) return [];
  if (inspection?.revision === revision && inspection.action !== "restore") return inspection.instances;
  return selectedInstances(items.filter((i) => i.revision === revision));
}
