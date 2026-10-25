import * as path from "node:path";
import * as vscode from "vscode";
import type { ServerManager } from "./server";
import { messageHtml, panelOptions, panelPage, type SelectionStore } from "./webview";

export const MODEL_VIEW_TYPE = "hitlcad.modelViewer";

/** Read-only 3D viewer for model files: the panel UI's file view, with the server converting the file. */
export class ModelViewerProvider implements vscode.CustomReadonlyEditorProvider {
  constructor(
    private readonly server: ServerManager,
    private readonly selections: SelectionStore,
    private readonly extensionUri: vscode.Uri,
  ) {}

  openCustomDocument(uri: vscode.Uri): vscode.CustomDocument {
    return { uri, dispose: () => undefined };
  }

  async resolveCustomEditor(document: vscode.CustomDocument, panel: vscode.WebviewPanel): Promise<void> {
    panel.webview.options = panelOptions(this.extensionUri);
    const name = path.basename(document.uri.fsPath);
    let disposed = false;
    panel.onDidDispose(() => (disposed = true));
    panel.webview.html = messageHtml(`Starting the hitlcad server to show ${name}…`);
    try {
      await this.server.ensure();
    } catch (e) {
      if (!disposed) panel.webview.html = messageHtml(`Could not show ${name}: ${(e as Error).message}`);
      return;
    }
    const html = await panelPage(panel.webview, this.extensionUri, { name: "file", path: document.uri.fsPath }, `hitlcad: ${name}`);
    if (disposed) return; // the tab was closed while the server started
    panel.webview.html = html;
    const tracking = this.selections.track(panel);
    panel.onDidDispose(() => tracking.dispose());
  }
}

