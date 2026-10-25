import * as vscode from "vscode";
import { parseUiMessage, UiStates } from "./lib/uiState";

/**
 * Keeps each design's shared UI state and relays it between that design's webviews: the 3D
 * editor tab and the Design view. A patch from either is merged and sent back to both.
 */
export class DesignUiHub {
  private readonly states = new UiStates();
  private readonly views = new Map<vscode.Webview, string>();

  /** `reveal` runs when a page asks for the Design view to come forward. */
  constructor(private readonly reveal: () => void) {}

  /** Relay `webview` while it shows design `designId`; dispose when it stops showing it. */
  attach(webview: vscode.Webview, designId: string): vscode.Disposable {
    this.views.set(webview, designId);
    const sub = webview.onDidReceiveMessage((m: unknown) => this.receive(webview, designId, m));
    return new vscode.Disposable(() => {
      sub.dispose();
      if (this.views.get(webview) === designId) this.views.delete(webview);
    });
  }

  private receive(webview: vscode.Webview, designId: string, m: unknown): void {
    const msg = parseUiMessage(m);
    if (msg?.kind === "ready") {
      void webview.postMessage({ type: "hitlcad.ui.state", state: this.states.get(designId) });
    } else if (msg?.kind === "patch") {
      const state = this.states.apply(designId, msg.patch);
      for (const [view, id] of this.views) if (id === designId) void view.postMessage({ type: "hitlcad.ui.state", state });
      if (msg.reveal) this.reveal();
    } else if (msg?.kind === "copy") {
      void vscode.commands.executeCommand("hitlcad.copyForChat");
    } else if (msg?.kind === "fullscreen") {
      void vscode.commands.executeCommand("workbench.action.toggleZenMode");
    } else if (msg?.kind === "save") {
      void vscode.commands.executeCommand("hitlcad.saveFile", msg.file);
    }
  }
}
