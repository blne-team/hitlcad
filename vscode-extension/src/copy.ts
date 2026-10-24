import * as vscode from "vscode";
import type { Api } from "./api";
import type { SelectionStore } from "./webview";

/**
 * Copy what the human points at, to paste into any chat: the selections and open pins of every
 * design in the workspace (the same text the agents' prompt hook adds), plus model-file selections.
 */
export async function copyForChat(api: Api, selections: SelectionStore): Promise<void> {
  const parts: string[] = [];
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const text = await api.context(folder.uri.fsPath);
    if (text) parts.push(text);
  }
  parts.push(...selections.all());
  if (!parts.length) {
    void vscode.window.showInformationMessage("Nothing is selected or pinned in the hitlcad views.");
    return;
  }
  await vscode.env.clipboard.writeText(parts.join("\n\n"));
  vscode.window.setStatusBarMessage("hitlcad: selections and pins copied — paste them into a chat.", 4000);
}
