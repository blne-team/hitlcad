import * as path from "node:path";

const DEFAULT_COMMAND = ["hitlcad", "serve"];
const DEV_COMMAND = ["uv", "run", "hitlcad", "serve"];

export interface WorkspaceFolderInfo {
  path: string;
  /** Contents of pyproject.toml, or null when there is none. */
  pyproject: string | null;
}

/** True for the hitlcad source checkout's pyproject.toml. */
export function isHitlcadPyproject(text: string | null): boolean {
  if (!text) return false;
  const project = /^\s*\[project\]\s*$([\s\S]*?)(?=^\s*\[|$(?![\s\S]))/m.exec(text);
  return /^\s*name\s*=\s*["']hitlcad["']\s*(?:#.*)?$/m.test(project ? project[1]! : text);
}

type Resolution =
  | { ok: true; command: string[]; cwd: string | undefined; source: "configured" | "installed" | "dev" }
  | { ok: false; error: string };

/**
 * Decide how to start the server.
 * - A user-configured command (not the default) is used as-is.
 * - The default `hitlcad serve` is used when `hitlcad` is on PATH (the installed tool).
 * - Otherwise, in a hitlcad source checkout, fall back to `uv run hitlcad serve` there.
 */
export function resolveServerCommand(opts: {
  configured: string[] | undefined;
  configuredCwd: string | undefined;
  folders: WorkspaceFolderInfo[];
  onPath: (exe: string) => boolean;
}): Resolution {
  const cwd = opts.configuredCwd?.trim() ? opts.configuredCwd.trim() : undefined;
  const configured = opts.configured?.filter((s) => s.length) ?? [];
  const isDefault = configured.length === 0 || configured.join("\u0000") === DEFAULT_COMMAND.join("\u0000");
  if (!isDefault) return { ok: true, command: configured, cwd: cwd ?? opts.folders[0]?.path, source: "configured" };
  if (opts.onPath(DEFAULT_COMMAND[0]!)) return { ok: true, command: DEFAULT_COMMAND, cwd, source: "installed" };
  const dev = opts.folders.find((f) => isHitlcadPyproject(f.pyproject));
  if (dev && opts.onPath(DEV_COMMAND[0]!)) return { ok: true, command: DEV_COMMAND, cwd: cwd ?? dev.path, source: "dev" };
  return {
    ok: false,
    error: "hitlcad is not installed — run `uv tool install <path-to-hitlcad>` (or set hitlcad.serverCommand).",
  };
}

/** Find an executable on PATH (no shell). */
export function whichSync(exe: string, envPath: string, isExecutable: (p: string) => boolean, pathExt = ""): string | null {
  if (exe.includes(path.sep) || exe.includes("/")) return isExecutable(exe) ? exe : null;
  const exts = pathExt ? ["", ...pathExt.split(";").filter(Boolean)] : [""];
  for (const dir of envPath.split(path.delimiter).filter(Boolean)) {
    for (const ext of exts) {
      const candidate = path.join(dir, exe + ext);
      if (isExecutable(candidate)) return candidate;
    }
  }
  return null;
}
