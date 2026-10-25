/**
 * UI state shared by the webviews of one design (the 3D editor tab and the Design view). The
 * panel pages define its fields; the extension only stores and merges what they send.
 */
type UiState = Record<string, unknown>;

/** A file of a design's revision, by its path in the revision; `name` is the file name to save it as. */
export interface RevisionFile {
  design: string;
  revision: number;
  path: string;
  name: string;
}

type UiMessage =
  | { kind: "ready" }
  | { kind: "patch"; patch: UiState; reveal: boolean }
  | { kind: "selection"; text: string }
  /** Copy the selections and open pins of the whole workspace, to paste into a chat. */
  | { kind: "copy" }
  /** Toggle VS Code's Zen mode: the model on the whole screen. */
  | { kind: "fullscreen" }
  /** Save a copy of a revision's file where the human chooses. */
  | { kind: "save"; file: RevisionFile };

const isPlainObject = (v: unknown): v is UiState => typeof v === "object" && v !== null && !Array.isArray(v);

/** A message a hitlcad page sent through its webview, or null for anything else. */
export function parseUiMessage(m: unknown): UiMessage | null {
  if (!isPlainObject(m)) return null;
  if (m.type === "hitlcad.ui.ready") return { kind: "ready" };
  if (m.type === "hitlcad.ui" && isPlainObject(m.patch)) return { kind: "patch", patch: m.patch, reveal: m.reveal === true };
  if (m.type === "hitlcad.selection" && typeof m.text === "string") return { kind: "selection", text: m.text };
  if (m.type === "hitlcad.copy") return { kind: "copy" };
  if (m.type === "hitlcad.fullscreen") return { kind: "fullscreen" };
  if (m.type === "hitlcad.saveFile" && typeof m.design === "string" && typeof m.revision === "number" && typeof m.path === "string" && typeof m.name === "string") {
    return { kind: "save", file: { design: m.design, revision: m.revision, path: m.path, name: m.name } };
  }
  return null;
}

/** Each design's UI state, merged from the patches its webviews send. */
export class UiStates {
  private readonly states = new Map<string, UiState>();

  get(designId: string): UiState {
    return this.states.get(designId) ?? {};
  }

  apply(designId: string, patch: UiState): UiState {
    const next = { ...this.get(designId), ...patch };
    this.states.set(designId, next);
    return next;
  }
}
