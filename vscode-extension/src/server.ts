import { type ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import type { Api } from "./api";
import { resolveServerCommand, whichSync, type WorkspaceFolderInfo } from "./lib/serverCommand";

const STARTUP_TIMEOUT_MS = 30_000;

/** Starts the hitlcad server on demand and stops it on deactivate if we started it. */
export class ServerManager implements vscode.Disposable {
  private child: ChildProcess | null = null;
  private starting: Promise<void> | null = null;
  private versionChecked = false;

  /** `version` is this extension's: the panel UI it ships expects the server of the same release. */
  constructor(
    private readonly api: Api,
    readonly output: vscode.OutputChannel,
    private readonly version: string,
  ) {}

  /** Resolve when the server answers /api/health, starting it if allowed. */
  async ensure(): Promise<void> {
    if (await this.api.health()) return this.checkVersion();
    if (!config().get<boolean>("autoStart")) {
      throw new Error(`The hitlcad server is not running at ${serverUrl()}. Start it or enable hitlcad.autoStart.`);
    }
    this.starting ??= this.start().finally(() => (this.starting = null));
    await this.starting;
    await this.checkVersion();
  }

  /** Warn once when the server runs another hitlcad release, e.g. one left running from before an upgrade. */
  private async checkVersion(): Promise<void> {
    if (this.versionChecked) return;
    this.versionChecked = true;
    const server = (await this.api.capabilities()).version;
    if (server === this.version) return;
    const message = `The hitlcad server at ${serverUrl()} is version ${server}, this extension ${this.version}. Stop that server so the extension starts its own, or install the matching release.`;
    this.output.appendLine(message);
    void vscode.window.showWarningMessage(`hitlcad: ${message}`);
  }

  private async start(): Promise<void> {
    const searchPath = augmentedPath();
    const resolution = resolveServerCommand({
      configured: config().get<string[]>("serverCommand"),
      configuredCwd: config().get<string>("serverCwd"),
      folders: workspaceFolderInfo(),
      which: (exe) => whichSync(exe, searchPath, isExecutable, process.platform === "win32"),
    });
    if (!resolution.ok) throw new Error(resolution.error);
    const [cmd, ...args] = resolution.command;
    const url = new URL(serverUrl());
    // PYTHONUTF8: the server speaks UTF-8 (its log, which we read, and every file), whatever the code page.
    // A URL writes an IPv6 host in brackets ([::1]); the server binds the address itself.
    const host = url.hostname.replace(/^\[(.*)\]$/, "$1");
    const env = { ...process.env, PATH: searchPath, PYTHONUTF8: "1", HITLCAD_HOST: host, HITLCAD_PORT: url.port || "80" };
    this.output.appendLine(`$ ${resolution.command.join(" ")}${resolution.cwd ? `   (in ${resolution.cwd})` : ""}`);

    const child = spawn(cmd!, args, { cwd: resolution.cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    this.child = child;
    for (const stream of [child.stdout, child.stderr]) {
      stream?.setEncoding("utf8"); // decodes across chunks: a character may arrive split in two
      stream?.on("data", (d: string) => this.output.append(d));
    }
    let running = true;
    const exited = new Promise<never>((_, reject) => {
      child.once("error", (e) => {
        running = false;
        reject(new Error(`Could not start ${cmd}: ${e.message}`));
      });
      child.once("exit", (code, signal) => {
        running = false;
        if (this.child === child) this.child = null;
        reject(new Error(`The hitlcad server exited (${signal ?? `code ${code}`}) before it was ready.`));
      });
    });
    exited.catch(() => undefined); // observed below; avoid unhandled rejections after startup

    const deadline = Date.now() + STARTUP_TIMEOUT_MS;
    const healthy = (async () => {
      while (running) {
        if (await this.api.health(1000)) return;
        if (Date.now() >= deadline) throw new Error(`The hitlcad server did not become ready within ${STARTUP_TIMEOUT_MS / 1000} s.`);
        await new Promise((r) => setTimeout(r, 500));
      }
    })();
    try {
      await Promise.race([healthy, exited]);
    } catch (e) {
      child.kill(); // a server that never became ready must not linger beside the next attempt's
      throw e;
    }
    this.output.appendLine(`hitlcad server ready at ${serverUrl()}`);
  }

  dispose(): void {
    if (this.child && this.child.exitCode === null) {
      this.output.appendLine("Stopping the hitlcad server started by this window.");
      this.child.kill("SIGTERM");
    }
    this.child = null;
  }
}

export function config(): vscode.WorkspaceConfiguration {
  return vscode.workspace.getConfiguration("hitlcad");
}

export function serverUrl(): string {
  return config().get<string>("serverUrl")!.replace(/\/+$/, "");
}

export async function showError(e: unknown, output: vscode.OutputChannel): Promise<void> {
  const message = e instanceof Error ? e.message : String(e);
  output.appendLine(`Error: ${message}`);
  const choice = await vscode.window.showErrorMessage(`hitlcad: ${message}`, "Show logs");
  if (choice === "Show logs") output.show(true);
}

function workspaceFolderInfo(): WorkspaceFolderInfo[] {
  return (vscode.workspace.workspaceFolders ?? []).map((f) => {
    const file = path.join(f.uri.fsPath, "pyproject.toml");
    let pyproject: string | null = null;
    try {
      pyproject = fs.readFileSync(file, "utf8");
    } catch {
      // no pyproject
    }
    return { path: f.uri.fsPath, pyproject };
  });
}

/** PATH plus the usual user tool folders, which GUI-launched editors often miss (uv tool install → ~/.local/bin). */
function augmentedPath(): string {
  const extra = [path.join(os.homedir(), ".local", "bin"), path.join(os.homedir(), ".cargo", "bin")];
  const parts = (process.env.PATH ?? "").split(path.delimiter).filter(Boolean);
  for (const p of extra) if (!parts.includes(p)) parts.push(p);
  return parts.join(path.delimiter);
}

function isExecutable(p: string): boolean {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}
