import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { findDesignDir, DESIGN_FILE } from "./lib/designLookup";

/** Folder of the design that contains `file`, bounded by the workspace folder it lives in. */
export function designDirFor(file: vscode.Uri): string | null {
  if (file.scheme !== "file") return null;
  const root = vscode.workspace.getWorkspaceFolder(file)?.uri.fsPath ?? null;
  return findDesignDir(file.fsPath, root, (p) => fs.existsSync(p));
}

export function activeDesignDir(): string | null {
  const doc = vscode.window.activeTextEditor?.document;
  return doc ? designDirFor(doc.uri) : null;
}

export function hasDesign(dir: string): boolean {
  return fs.existsSync(path.join(dir, DESIGN_FILE));
}
