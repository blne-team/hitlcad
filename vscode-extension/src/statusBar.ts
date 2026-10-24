import * as vscode from "vscode";
import { designDirFor, designName } from "./designs";

/** "hitlcad: <name>" while the active file belongs to a design folder. */
export function registerStatusBar(ctx: vscode.ExtensionContext): void {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  item.command = "hitlcad.showPanel";
  item.tooltip = "Show the hitlcad panel for this design";
  const update = () => {
    const doc = vscode.window.activeTextEditor?.document;
    const dir = doc ? designDirFor(doc.uri) : null;
    if (!dir) {
      item.hide();
      return;
    }
    item.text = `$(symbol-misc) hitlcad: ${designName(dir)}`;
    item.show();
  };
  ctx.subscriptions.push(
    item,
    vscode.window.onDidChangeActiveTextEditor(update),
    vscode.workspace.onDidSaveTextDocument((d) => {
      if (d.fileName.endsWith("design.json")) update();
    }),
  );
  update();
}
