import type { SelectionItem, Vec3 } from "../api/types";
import type { CadMarker } from "./cadView";
import { toExtension } from "./host";

const fmt = (v: Vec3) => v.map((x) => x.toFixed(1)).join(", ");

/** The server's label usually ends with the point already; add it only when missing. */
export const hasPoint = (label: string) => / at \(/.test(label);

/** Text a human pastes into an agent chat: "In design Hook (revision 3) I selected: 1. …". */
export function selectionText(context: string, items: SelectionItem[]): string {
  if (!items.length) return "";
  const lines = items.map((it, i) => `${i + 1}. ${it.label}${hasPoint(it.label) ? "" : ` at (${fmt(it.point)}) mm`}`);
  return `${context} I selected:\n${lines.join("\n")}`;
}

/** How a selected item is drawn in the 3D view: a whole part as its bounding box, anything else as a ring. */
export function selectionMarker(item: SelectionItem): CadMarker {
  const box = item.kind === "solid" ? (item.details.bbox as [Vec3, Vec3]) : undefined;
  return { id: item.id, kind: "selection", point: item.point, normal: item.normal ?? null, label: item.label, box };
}

/** Give the extension a model file's selection text, which "Copy for chat" includes. */
export function notifyHost(text: string): void {
  toExtension({ type: "hitlcad.selection", text });
}

/** Ask VS Code to copy the selections and open pins of the whole workspace, to paste into a chat. */
export function copyForChat(): void {
  toExtension({ type: "hitlcad.copy" });
}
