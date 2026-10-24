import * as vscode from "vscode";
import type { DesignUiHub } from "./designUi";
import type { DesignRef } from "./panels";
import { messageHtml, panelOptions, panelPage } from "./webview";

export const INSPECTOR_VIEW = "hitlcad.inspector";

/**
 * The "Design" view: pins, parameters, changes, checks and brief of the design whose 3D editor
 * tab is active. A VS Code view, so it can be dragged to the bottom panel or either sidebar.
 */
export class DesignInspector implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | null = null;
  private design: DesignRef | null = null;
  private relay: vscode.Disposable | null = null;

  constructor(
    private readonly hub: DesignUiHub,
    private readonly extensionUri: vscode.Uri,
  ) {}

  resolveWebviewView(view: vscode.WebviewView): Promise<void> {
    this.view = view;
    view.webview.options = panelOptions(this.extensionUri);
    view.onDidDispose(() => {
      this.relay?.dispose();
      this.relay = null;
      this.view = null;
    });
    return this.render();
  }

  /** Show `design` (the active 3D editor's), or nothing when no design is open. */
  follow(design: DesignRef | null): void {
    if (design?.id === this.design?.id) return;
    this.design = design;
    void this.render();
  }

  /** Bring the view forward and focus it (e.g. to type a new pin's note). */
  reveal(): void {
    void vscode.commands.executeCommand(`${INSPECTOR_VIEW}.focus`);
  }

  dispose(): void {
    this.relay?.dispose();
  }

  private async render(): Promise<void> {
    const view = this.view;
    if (!view) return;
    this.relay?.dispose();
    this.relay = null;
    const design = this.design;
    view.description = design?.name ?? "";
    if (!design) {
      view.webview.html = messageHtml("Open a design to see its pins, parameters, changes and checks here.");
      return;
    }
    const html = await panelPage(view.webview, this.extensionUri, { name: "inspector", id: design.id }, `hitlcad: ${design.name}`);
    if (this.view !== view || this.design !== design) return; // another design was shown meanwhile
    this.relay = this.hub.attach(view.webview, design.id);
    view.webview.html = html;
  }
}
