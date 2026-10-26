import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { askAgentSetup } from "./agents";
import { type Api, ApiError, type DesignInfo } from "./api";
import { activeDesignDir, hasDesign } from "./designs";
import { DESIGN_FILE, PROJECT_DESIGNS } from "./lib/designLookup";
import type { RevisionFile } from "./lib/uiState";
import { slugify } from "./lib/slug";
import { MODEL_VIEW_TYPE } from "./modelViewer";
import { usePython } from "./python";
import type { DesignPanels, DesignRef } from "./panels";
import { type ServerManager, serverUrl, showError } from "./server";
import type { SelectionStore } from "./webview";
import { copyForChat } from "./copy";

interface Deps {
  api: Api;
  server: ServerManager;
  panels: DesignPanels;
  selections: SelectionStore;
}

const toRef = (d: DesignInfo): DesignRef => ({ path: d.path, id: d.id, name: d.name });
const firstFolder = () => vscode.workspace.workspaceFolders?.[0]?.uri;

export function registerCommands(ctx: vscode.ExtensionContext, deps: Deps): void {
  const { api, server, panels, selections } = deps;

  /** Wrap a command: make sure the server is up, and turn failures into one clear message. */
  const command = (id: string, fn: (...args: never[]) => Promise<unknown>, needsServer = true) =>
    ctx.subscriptions.push(
      vscode.commands.registerCommand(id, async (...args: never[]) => {
        try {
          if (needsServer) await server.ensure();
          await fn(...args);
        } catch (e) {
          await showError(e, server.output);
        }
      }),
    );

  const openFolderDesign = async (dir: string): Promise<DesignRef | null> => {
    try {
      return toRef(await api.openDesign(dir));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        const choice = await vscode.window.showInformationMessage(
          `${path.basename(dir)} is not a hitlcad design yet (no design.json).`,
          "Create Design Here",
        );
        if (choice === "Create Design Here") return createDesign(dir, path.basename(dir));
        return null;
      }
      throw e;
    }
  };

  const createDesign = async (dir: string, suggestedName: string): Promise<DesignRef | null> => {
    const caps = await api.capabilities();
    const backends = caps.backends.filter((b) => b.available);
    if (!backends.length) throw new Error("No CAD backend is available on the server (see hitlcad doctor).");
    const picked =
      backends.length === 1
        ? backends[0]
        : await vscode.window.showQuickPick(
            backends.map((b) => ({ label: b.title, description: b.name, detail: b.summary, backend: b })),
            { title: "Modelling backend", placeHolder: "build123d for precise parts, Blender for organic shapes" },
          ).then((p) => p?.backend);
    if (!picked) return null;
    fs.mkdirSync(dir, { recursive: true });
    const design = await api.openDesign(dir, { name: suggestedName, backend: picked.name });
    void vscode.window.showInformationMessage(
      `hitlcad: ${design.name} is ready. Describe the product to Claude Code or Codex in their chat: they declare its parts in design.json first, and every save rebuilds the panel.`,
    );
    return toRef(design);
  };

  /** The design to act on: the active editor's folder, the focused panel, or a pick from known designs. */
  const currentDesign = async (): Promise<DesignRef | null> => {
    const dir = activeDesignDir();
    if (dir) return openFolderDesign(dir);
    const panel = panels.active();
    if (panel) return panel;
    const known = await api.designs();
    if (!known.length) {
      const choice = await vscode.window.showInformationMessage("No hitlcad design is open.", "New Design", "Open Design");
      if (choice) await vscode.commands.executeCommand(choice === "New Design" ? "hitlcad.newDesign" : "hitlcad.openDesign");
      return null;
    }
    const pick = await vscode.window.showQuickPick(
      known.map((d) => ({ label: d.name, description: d.backend, detail: d.path, design: d })),
      { title: "Choose a design" },
    );
    return pick ? toRef(pick.design) : null;
  };

  const openEditorAndPanel = async (design: DesignRef) => {
    const structure = path.join(design.path, DESIGN_FILE); // the structure: parts, connectors, mates
    if (fs.existsSync(structure)) {
      await vscode.window.showTextDocument(vscode.Uri.file(structure), { viewColumn: vscode.ViewColumn.One, preview: false });
    }
    await panels.show(design, vscode.ViewColumn.Beside);
  };

  // ------------------------------------------------------------------ projects & designs

  command("hitlcad.newProject", async () => {
    const picked = await vscode.window.showOpenDialog({
      canSelectFolders: true,
      canSelectFiles: false,
      canSelectMany: false,
      defaultUri: firstFolder() ?? vscode.Uri.file(os.homedir()),
      openLabel: "Use for design project",
      title: "Choose a folder for the design project (an empty one is best)",
    });
    if (!picked?.[0]) return;
    let dir = picked[0].fsPath;
    const entries = fs.readdirSync(dir).filter((n) => !n.startsWith("."));
    if (entries.length) {
      const choice = await vscode.window.showWarningMessage(
        `${path.basename(dir)} is not empty. Existing files are never overwritten.`,
        { modal: true },
        "Use This Folder",
        "Create a Subfolder…",
      );
      if (!choice) return;
      if (choice === "Create a Subfolder…") {
        const name = await vscode.window.showInputBox({ title: "Project folder name", value: "cad-designs", validateInput: (v) => (slugify(v) ? null : "Use letters or digits") });
        if (!name) return;
        dir = path.join(dir, slugify(name));
        fs.mkdirSync(dir, { recursive: true });
      }
    }
    const result = await api.createProject(dir, await askAgentSetup(dir, await api.agentSetup(dir)));
    const created = result.created.length ? `Created ${result.created.join(", ")}.` : "Everything was already in place.";
    const folder = (vscode.workspace.workspaceFolders ?? []).find((f) => path.resolve(f.uri.fsPath) === path.resolve(result.path));
    if (folder) {
      await usePython(ctx, folder, (await api.capabilities()).python);
      const next = await vscode.window.showInformationMessage(`Design project ready. ${created}`, "New Design");
      if (next) await vscode.commands.executeCommand("hitlcad.newDesign");
      return;
    }
    const open = await vscode.window.showInformationMessage(`Design project ready in ${result.path}. ${created}`, "Open Project", "Open in New Window");
    if (open) {
      await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(result.path), { forceNewWindow: open === "Open in New Window" });
    }
  });

  command("hitlcad.newDesign", async () => {
    // A project keeps its designs in designs/, so the name is all there is to ask; elsewhere, where first.
    // (Open Design on any folder without a design.json offers to create one there.)
    const root = firstFolder();
    const designsDir = root ? path.join(root.fsPath, PROJECT_DESIGNS) : null;
    let parent = designsDir && fs.existsSync(designsDir) ? designsDir : null;
    if (!parent) {
      const picked = await vscode.window.showOpenDialog({
        canSelectFolders: true,
        canSelectFiles: false,
        canSelectMany: false,
        defaultUri: root ?? vscode.Uri.file(os.homedir()),
        openLabel: "Create design here",
        title: "Where should the design folder go?",
      });
      if (!picked?.[0]) return;
      parent = picked[0].fsPath;
    }
    const where = parent;
    const name = await vscode.window.showInputBox({
      title: "New design",
      placeHolder: "Wall hook",
      validateInput: (v) => {
        if (!v.trim()) return "Enter a name";
        if (!slugify(v)) return "Use at least one letter or digit";
        return { message: `Creates ${path.join(where, slugify(v))}`, severity: vscode.InputBoxValidationSeverity.Info };
      },
    });
    if (!name) return;
    const dir = path.join(where, slugify(name));
    if (hasDesign(dir)) {
      const choice = await vscode.window.showInformationMessage(`${path.basename(dir)} already exists.`, "Open It");
      if (choice) {
        const existing = await openFolderDesign(dir);
        if (existing) await openEditorAndPanel(existing);
      }
      return;
    }
    const design = await createDesign(dir, name.trim());
    if (design) await openEditorAndPanel(design);
  });

  command("hitlcad.openDesign", async (uri?: vscode.Uri) => {
    let dir = uri?.fsPath ?? activeDesignDir();
    if (!dir) {
      const picked = await vscode.window.showOpenDialog({
        canSelectFolders: true,
        canSelectFiles: false,
        defaultUri: firstFolder(),
        openLabel: "Open design",
        title: "Choose a design folder (it contains design.json)",
      });
      dir = picked?.[0]?.fsPath ?? null;
    }
    if (!dir) return;
    const design = await openFolderDesign(dir);
    if (design) await openEditorAndPanel(design);
  });

  command("hitlcad.showPanel", async () => {
    const design = await currentDesign();
    if (design) await panels.show(design, vscode.ViewColumn.Beside);
  });

  command("hitlcad.rebuild", async () => {
    const design = await currentDesign();
    if (!design) return;
    await vscode.workspace.saveAll(false);
    const rev = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `hitlcad: building ${design.name}…` },
      () => api.buildDesign(design.id),
    );
    if (rev.report.status === "succeeded") {
      vscode.window.setStatusBarMessage(`hitlcad: revision ${rev.id} built in ${rev.report.duration_s.toFixed(1)} s`, 5000);
      return;
    }
    const problem = rev.report.problems[0];
    const lead = problem ? rev.report.results[problem.findings[0]!.results[0]!] : undefined;
    const why = lead ? `${lead.subject}: ${lead.detail.split("\n").slice(-1)[0]}` : "no result says why";
    const choice = await vscode.window.showErrorMessage(`hitlcad: revision ${rev.id} failed: ${why}`, "Show Panel");
    if (choice) await panels.show(design);
  });

  // ------------------------------------------------------------------ agents

  command("hitlcad.configureAgents", async () => {
    const root = firstFolder();
    if (!root) throw new Error("Open the design project's folder first.");
    const allowed = await askAgentSetup(root.fsPath, await api.agentSetup(root.fsPath));
    if (!allowed.hooks && !allowed.codex) return;
    const result = await api.createProject(root.fsPath, allowed);
    const changed = result.created.length ? `Updated ${result.created.join(", ")}.` : "Everything was already in place.";
    const approve = allowed.hooks ? " In Codex, approve hitlcad's hook once (/hooks)." : "";
    void vscode.window.showInformationMessage(`hitlcad: ${changed}${approve}`);
  });

  // ------------------------------------------------------------------ viewer & selection

  command("hitlcad.openInViewer", async (uri?: vscode.Uri) => {
    const target = uri ?? vscode.window.activeTextEditor?.document.uri;
    if (!target) throw new Error("Select a model file (STL, STEP, GLB, 3MF, OBJ, PLY) in the explorer.");
    await vscode.commands.executeCommand("vscode.openWith", target, MODEL_VIEW_TYPE);
  });

  command("hitlcad.copyForChat", () => copyForChat(api, selections));

  // From a design's Results: a copy of one of a revision's files (they live in the server's data folder).
  command("hitlcad.saveFile", async (file: RevisionFile) => {
    const target = await vscode.window.showSaveDialog({
      defaultUri: vscode.Uri.joinPath(firstFolder() ?? vscode.Uri.file(os.homedir()), file.name),
      title: `Save ${file.path} of revision ${file.revision}`,
    });
    if (!target) return;
    await vscode.workspace.fs.writeFile(target, await api.revisionFile(file));
    vscode.window.setStatusBarMessage(`hitlcad: saved ${path.basename(target.fsPath)}`, 4000);
  });

  // ------------------------------------------------------------------ server

  command("hitlcad.startServer", async () => {
    vscode.window.setStatusBarMessage(`hitlcad server is running at ${serverUrl()}`, 4000);
  });
  command("hitlcad.showLogs", async () => server.output.show(true), false);
}
