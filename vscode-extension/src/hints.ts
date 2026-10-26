import * as vscode from "vscode";

/** Whether the 3D views show the hint on how to select. */
export const selectionHint = (): boolean => vscode.workspace.getConfiguration("hitlcad").get("selectionHint", true);

/** The human closed the hint on how to select, in any 3D view: turn it off for every one, from now on. */
export function hideSelectionHint(): void {
  void vscode.workspace.getConfiguration("hitlcad").update("selectionHint", false, vscode.ConfigurationTarget.Global);
}
