import * as path from "node:path";

const DEFAULT_COMMAND = ["hitlcad", "serve"];

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

type Resolution = { ok: true; command: string[]; cwd: string | undefined } | { ok: false; error: string };

/**
 * Decide how to start the server.
 * - A user-configured command (not the default) is used as-is.
 * - The default `hitlcad serve` is used when `hitlcad` is on PATH (the installed tool).
 * - Otherwise, in a hitlcad source checkout, fall back to `uv run hitlcad serve` there.
 * `which` finds an executable on PATH; the command found runs by that full path.
 */
export function resolveServerCommand(opts: {
  configured: string[] | undefined;
  configuredCwd: string | undefined;
  folders: WorkspaceFolderInfo[];
  which: (exe: string) => string | null;
}): Resolution {
  const cwd = opts.configuredCwd?.trim() ? opts.configuredCwd.trim() : undefined;
  const configured = opts.configured?.filter((s) => s.length) ?? [];
  const isDefault = configured.length === 0 || configured.join("\u0000") === DEFAULT_COMMAND.join("\u0000");
  if (!isDefault) return { ok: true, command: configured, cwd: cwd ?? opts.folders[0]?.path };
  const hitlcad = opts.which("hitlcad");
  if (hitlcad) return { ok: true, command: [hitlcad, "serve"], cwd };
  const dev = opts.folders.find((f) => isHitlcadPyproject(f.pyproject));
  const uv = dev && opts.which("uv");
  if (dev && uv) return { ok: true, command: [uv, "run", "hitlcad", "serve"], cwd: cwd ?? dev.path };
  return {
    ok: false,
    error: "hitlcad is not installed — run `uv tool install <path-to-hitlcad>` (or set hitlcad.serverCommand).",
  };
}

/**
 * Find an executable on PATH, to run without a shell. On Windows only an .exe or .com: Node refuses to
 * spawn a .cmd or .bat without a shell (since the 2024 batch-file fix), and uv installs tools as .exe.
 */
export function whichSync(exe: string, envPath: string, isExecutable: (p: string) => boolean, windows: boolean): string | null {
  const exts = windows ? [".exe", ".com"] : [""];
  for (const dir of envPath.split(path.delimiter).filter(Boolean)) {
    for (const ext of exts) {
      const candidate = path.join(dir, exe + ext);
      if (isExecutable(candidate)) return candidate;
    }
  }
  return null;
}
