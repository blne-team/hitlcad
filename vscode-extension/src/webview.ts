import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { esc, panelHtml, type PanelView } from "./lib/panelHtml";
import { parseUiMessage } from "./lib/uiState";
import { serverUrl } from "./server";

/** Webview options for a panel page: scripts on, and only the built panel UI (out/ui/) readable. */
export function panelOptions(extensionUri: vscode.Uri): vscode.WebviewOptions {
  return { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(extensionUri, "out", "ui")] };
}

/** The page that runs the panel UI in `webview`, talking to the server at the URL the webview can reach (also in remote setups). */
export async function panelPage(webview: vscode.Webview, extensionUri: vscode.Uri, view: PanelView, title: string): Promise<string> {
  const server = (await vscode.env.asExternalUri(vscode.Uri.parse(serverUrl()))).toString(true).replace(/\/+$/, "");
  return panelHtml({
    assets: webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "out", "ui")).toString(),
    cspSource: webview.cspSource,
    nonce: randomBytes(16).toString("base64"),
    title,
    config: { server, view },
  });
}

/** A plain page with one line of text, for states without a hitlcad page (starting, nothing open, errors). */
export function messageHtml(text: string): string {
  return `<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>body{font-family:var(--vscode-font-family);color:var(--vscode-descriptionForeground);padding:12px 20px}</style></head><body><p>${esc(text)}</p></body></html>`;
}

/** Latest selection text of each model-file viewer (designs keep theirs on the server). */
export class SelectionStore {
  private readonly texts = new Map<vscode.WebviewPanel, string>();

  track(panel: vscode.WebviewPanel): vscode.Disposable {
    const subs = [
      panel.webview.onDidReceiveMessage((m: unknown) => {
        const msg = parseUiMessage(m);
        if (msg?.kind === "copy") void vscode.commands.executeCommand("hitlcad.copyForChat");
        if (msg?.kind !== "selection") return;
        if (msg.text) this.texts.set(panel, msg.text);
        else this.texts.delete(panel);
      }),
      panel.onDidDispose(() => this.texts.delete(panel)),
    ];
    return vscode.Disposable.from(...subs);
  }

  all(): string[] {
    return [...this.texts.values()];
  }
}
