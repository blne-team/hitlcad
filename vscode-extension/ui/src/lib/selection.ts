import type { BuildReport, SelectionItem, Vec3 } from "../api/types";
import type { CadMarker } from "./cadView";
import { toExtension } from "./host";

/** Text a human pastes into an agent chat: "In part.step I selected: 1. …". The server's labels say where. */
export function selectionText(context: string, items: SelectionItem[]): string {
  if (!items.length) return "";
  return `${context} I selected:\n${items.map((it, i) => `${i + 1}. ${it.label}`).join("\n")}`;
}

/** How a selected item is drawn in the 3D view: a whole part as its bounding box, anything else as a ring. */
export function selectionMarker(item: SelectionItem): CadMarker {
  const box = item.kind === "solid" ? (item.details.bbox as [Vec3, Vec3]) : undefined;
  return { id: item.id, kind: "selection", point: item.point, instance: item.instance ?? item.component?.instance ?? null, normal: item.normal, label: item.label, box };
}

/**
 * What the view calls each shape of a built design, by its path under the design: the leaf instance it belongs to
 * ("tilt/planet[2]"), and the part it is an instance of where its name does not say so ("lid_screw[3] · screw_m3").
 */
export function partNames(report: BuildReport): (path: string) => string {
  const parts = new Map<string, string>();
  const walk = (r: BuildReport, prefix: string) => {
    for (const instance of r.instances) {
      const path = prefix + instance.path;
      if (instance.component) walk(r.components[instance.component]!, `${path}/`);
      else if (instance.part) parts.set(path, instance.part);
    }
  };
  walk(report, "");
  return (path) => {
    // A part of several colours is a group of shapes under its instance: the instance names them all
    let at = path;
    while (at && !parts.has(at)) at = at.slice(0, Math.max(at.lastIndexOf("/"), 0));
    const part = parts.get(at);
    if (part === undefined) return path;
    const own = at.slice(at.lastIndexOf("/") + 1).replace(/\[\d+\]$/, "");
    return own === part ? at : `${at} · ${part}`;
  };
}

/** Give the extension a model file's selection text, which "Copy for chat" includes. */
export function notifyHost(text: string): void {
  toExtension({ type: "hitlcad.selection", text });
}

/** Ask VS Code to copy the selections and open pins of the whole workspace, to paste into a chat. */
export function copyForChat(): void {
  toExtension({ type: "hitlcad.copy" });
}
