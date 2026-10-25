import type { PythonExtension } from "@vscode/python-extension";
import * as vscode from "vscode";

const PYTHON_EXTENSION = "ms-python.python";
const SET_TO = "hitlcad.python.setTo";

/**
 * Point VS Code's Python extension at the interpreter hitlcad builds models with, for the project's folder, so
 * part files resolve their imports (build123d). The Python extension keeps that choice in this user's state of
 * the workspace, not in a committed setting. Done once per interpreter, so one the human picks afterwards stays.
 */
export async function usePython(ctx: vscode.ExtensionContext, folder: vscode.WorkspaceFolder, python: string): Promise<void> {
  const extension = vscode.extensions.getExtension<PythonExtension>(PYTHON_EXTENSION);
  if (!extension || ctx.workspaceState.get<string>(SET_TO) === python) return;
  const api = extension.isActive ? extension.exports : await extension.activate();
  await api.environments.updateActiveEnvironmentPath(python, folder.uri);
  await ctx.workspaceState.update(SET_TO, python);
}
