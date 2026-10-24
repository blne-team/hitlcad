import * as path from "node:path";
import * as vscode from "vscode";
import type { DesignUiHub } from "./designUi";
import { panelOptions, panelPage } from "./webview";

export interface DesignRef {
  path: string;
  id: string;
  name: string;
}

const DESIGN_VIEW_TYPE = "hitlcad.design";

/** One webview panel per design folder, showing the design's 3D editor. */
export class DesignPanels implements vscode.Disposable {
  private readonly panels = new Map<string, { panel: vscode.WebviewPanel; design: DesignRef }>();
  private lastActive: DesignRef | null = null;
  private readonly activeChanged = new vscode.EventEmitter<DesignRef | null>();
  /** The design the Design view should follow: the focused panel's, else the last one shown, else none. */
  readonly onDidChangeActive = this.activeChanged.event;

  constructor(
    private readonly hub: DesignUiHub,
    private readonly extensionUri: vscode.Uri,
  ) {}

  /** Open or reveal the design's panel. `quiet` keeps the keyboard focus where it is (e.g. an agent's chat). */
  async show(design: DesignRef, column: vscode.ViewColumn = vscode.ViewColumn.Beside, quiet = false): Promise<void> {
    const key = path.resolve(design.path);
    const existing = this.panels.get(key);
    if (existing && existing.design.id === design.id) {
      existing.panel.reveal(quiet ? undefined : column, quiet);
      this.setActive(design);
      return;
    }
    existing?.panel.dispose();
    const title = `hitlcad: ${design.name}`;
    const panel = vscode.window.createWebviewPanel(DESIGN_VIEW_TYPE, title, { viewColumn: column, preserveFocus: true }, {
      ...panelOptions(this.extensionUri),
      retainContextWhenHidden: true,
    });
    let relay: vscode.Disposable | undefined;
    panel.onDidChangeViewState(() => {
      if (panel.active) this.setActive(design);
    });
    panel.onDidDispose(() => {
      relay?.dispose();
      if (this.panels.get(key)?.panel === panel) this.panels.delete(key);
      if (this.lastActive?.id === design.id) this.setActive([...this.panels.values()].pop()?.design ?? null);
    });
    this.panels.set(key, { panel, design });
    this.setActive(design);
    const html = await panelPage(panel.webview, this.extensionUri, { name: "design", id: design.id }, title);
    if (this.panels.get(key)?.panel !== panel) return; // closed or replaced meanwhile
    relay = this.hub.attach(panel.webview, design.id);
    panel.webview.html = html;
  }

  /** True when the design's panel is on screen (open and not hidden behind another tab). */
  isVisible(designPath: string): boolean {
    return this.panels.get(path.resolve(designPath))?.panel.visible ?? false;
  }

  /** The design whose panel is focused, else the one shown most recently. */
  active(): DesignRef | null {
    for (const { panel, design } of this.panels.values()) if (panel.active) return design;
    return this.lastActive;
  }

  dispose(): void {
    for (const { panel } of this.panels.values()) panel.dispose();
    this.panels.clear();
    this.activeChanged.dispose();
  }

  private setActive(design: DesignRef | null): void {
    if (design?.id === this.lastActive?.id) return;
    this.lastActive = design;
    this.activeChanged.fire(design);
  }
}
