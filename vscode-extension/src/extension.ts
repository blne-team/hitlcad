import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { ActivityWatcher, type BuildActivity } from "./activity";
import { offerAgentSetup } from "./agents";
import { Api } from "./api";
import { registerCommands } from "./commands";
import { DesignUiHub } from "./designUi";
import { DesignInspector, INSPECTOR_VIEW } from "./inspector";
import { DESIGN_FILE, PROJECT_DESIGNS } from "./lib/designLookup";
import { isHitlcadPyproject } from "./lib/serverCommand";
import { MODEL_VIEW_TYPE, ModelViewerProvider } from "./modelViewer";
import { DesignPanels } from "./panels";
import { usePython } from "./python";
import { config, ServerManager, serverUrl } from "./server";
import { trackSaves } from "./saving";
import { registerStatusBar } from "./statusBar";
import { SelectionStore } from "./webview";

export function activate(ctx: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("hitlcad");
  const api = new Api(serverUrl);
  const server = new ServerManager(api, output, ctx.extension.packageJSON.version as string);
  const selections = new SelectionStore();
  const hub = new DesignUiHub(() => inspector.reveal());
  const inspector = new DesignInspector(hub, ctx.extensionUri);
  const panels = new DesignPanels(hub, ctx.extensionUri);
  ctx.subscriptions.push(
    output,
    server,
    panels,
    inspector,
    vscode.window.registerWebviewViewProvider(INSPECTOR_VIEW, inspector, { webviewOptions: { retainContextWhenHidden: true } }),
    panels.onDidChangeActive((design) => inspector.follow(design)),
  );

  registerCommands(ctx, { api, server, panels, selections });
  ctx.subscriptions.push(trackSaves(api));
  registerStatusBar(ctx);
  ctx.subscriptions.push(
    vscode.window.registerCustomEditorProvider(MODEL_VIEW_TYPE, new ModelViewerProvider(server, selections, ctx.extensionUri), {
      webviewOptions: { retainContextWhenHidden: true },
      supportsMultipleEditorsPerDocument: true,
    }),
  );

  // In workspaces that use hitlcad: follow builds, start the server if needed, point VS Code's Python at the
  // interpreter hitlcad builds with, then offer the agent setup a design project lacks (it changes agent config,
  // so only with the human's permission).
  let following = false;
  const follow = () => {
    if (following) return;
    following = true;
    const activity = new ActivityWatcher(serverUrl, (build) => void onBuild(build, panels));
    ctx.subscriptions.push(activity);
    activity.start();
  };
  // A window whose first design appears later (New Design, an agent's create_design) follows builds from then on.
  const appearing = vscode.workspace.createFileSystemWatcher(`**/${DESIGN_FILE}`, false, true, true);
  ctx.subscriptions.push(appearing, appearing.onDidCreate(follow));
  void (async () => {
    if (!(await usesHitlcad())) return;
    follow();
    await server.ensure();
    const root = vscode.workspace.workspaceFolders?.[0];
    if (!root || !fs.existsSync(path.join(root.uri.fsPath, PROJECT_DESIGNS))) return;
    await usePython(ctx, root, (await api.capabilities()).python);
    await offerAgentSetup(ctx, await api.agentSetup(root.uri.fsPath), () => vscode.commands.executeCommand("hitlcad.configureAgents"));
  })().catch((e: Error) => output.appendLine(`Startup: ${e.message}`));
}

/** Bring a fresh build of a design in this workspace into view, as `hitlcad.autoOpenPanel` says. */
async function onBuild(build: BuildActivity, panels: DesignPanels): Promise<void> {
  const mode = config().get<string>("autoOpenPanel");
  if (mode === "off" || !vscode.workspace.getWorkspaceFolder(vscode.Uri.file(build.path))) return;
  if (panels.isVisible(build.path)) return; // it updates live
  const design = { path: build.path, id: build.id, name: build.name };
  if (mode === "open") {
    await panels.show(design, vscode.ViewColumn.Beside, true);
    return;
  }
  const what = build.status === "succeeded" ? `revision ${build.revision} built` : `revision ${build.revision} failed`;
  const choice = await vscode.window.showInformationMessage(`hitlcad: ${build.name} ${what}.`, "Show");
  if (choice) await panels.show(design);
}

async function usesHitlcad(): Promise<boolean> {
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const root = folder.uri.fsPath;
    if (fs.existsSync(path.join(root, DESIGN_FILE)) || fs.existsSync(path.join(root, PROJECT_DESIGNS))) return true;
    try {
      const mcp = fs.readFileSync(path.join(root, ".mcp.json"), "utf8");
      if (mcp.includes('"hitlcad"')) return true;
    } catch {
      // no .mcp.json
    }
    try {
      if (isHitlcadPyproject(fs.readFileSync(path.join(root, "pyproject.toml"), "utf8"))) return true;
    } catch {
      // no pyproject
    }
  }
  const found = await vscode.workspace.findFiles(`**/${DESIGN_FILE}`, "**/node_modules/**", 1);
  return found.length > 0;
}
