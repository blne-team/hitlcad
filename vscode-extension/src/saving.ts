import * as crypto from "node:crypto";
import * as path from "node:path";
import * as vscode from "vscode";
import type { Api } from "./api";
import { DESIGN_FILE } from "./lib/designLookup";

/**
 * Tell hitlcad, before VS Code saves a design's file, what the editor's buffer was read from: a buffer older than
 * the file (the panel, an agent or hitlcad wrote it meanwhile) is then merged from there, so what was written since
 * stays instead of being undone by the save. A buffer is as it was read whenever it is not dirty: when it opens, and
 * after VS Code saves or reloads it.
 */
export function trackSaves(api: Api): vscode.Disposable {
  const read = new Map<string, string>(); // file path -> sha256 of what its buffer was last read as
  const note = (doc: vscode.TextDocument) => {
    if (designFile(doc) && !doc.isDirty) read.set(doc.uri.fsPath, crypto.createHash("sha256").update(doc.getText()).digest("hex"));
  };
  vscode.workspace.textDocuments.forEach(note);
  return vscode.Disposable.from(
    vscode.workspace.onDidOpenTextDocument(note),
    vscode.workspace.onDidChangeTextDocument((e) => note(e.document)),
    vscode.workspace.onDidSaveTextDocument(note),
    vscode.workspace.onDidCloseTextDocument((doc) => read.delete(doc.uri.fsPath)),
    vscode.workspace.onWillSaveTextDocument((e) => {
      const was = read.get(e.document.uri.fsPath);
      if (was) e.waitUntil(api.saving(e.document.uri.fsPath, was).catch(() => undefined)); // no server: an ordinary save
    }),
  );
}

/** The files a design's document holds as text (`workspace.document.is_text`). */
function designFile(doc: vscode.TextDocument): boolean {
  return doc.uri.scheme === "file" && (path.basename(doc.uri.fsPath) === DESIGN_FILE || doc.uri.fsPath.endsWith(".py"));
}
