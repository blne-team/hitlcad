# Installing hitlcad

hitlcad is two pieces, released together:

- **the hitlcad tool** (`hitlcad-<version>-py3-none-any.whl`): the server that builds and checks
  designs, the MCP tools your coding agent calls, the CLI, and the panel UI's API;
- **the VS Code extension** (`hitlcad-<version>.vsix`): the 3D editor, the **hitlcad › Design**
  view and the commands. It starts the tool when you open a design project.

Both come in one folder, `hitlcad-<version>/`, with an installer. Get
`hitlcad-<version>.tar.gz` from the [GitHub releases](https://github.com/blne-team/solidwork/releases)
and unpack it (the release also attaches the wheel and the `.vsix` on their own).

## Requirements

| | |
| --- | --- |
| [VS Code](https://code.visualstudio.com/) 1.101 or newer | desktop; Remote-SSH, WSL and Dev Containers work too (see below) |
| the `code` command on PATH | Command Palette: **Shell Command: Install 'code' command in PATH** |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | installs hitlcad and the Python it runs on (3.11 to 3.13; the release names a default in `python-version`) |
| [Claude Code](https://code.claude.com/) or [Codex](https://developers.openai.com/codex/) in VS Code | they do the modelling; hitlcad has no agent of its own |
| [Blender 4.5 LTS](https://www.blender.org/download/lts/), optional | for Blender (mesh) designs and the preview renders; build123d designs work without it |

Linux, macOS and Windows are supported.

## Install

**macOS and Linux**, in the unpacked folder:

```bash
./install.sh
```

It installs the tool with `uv tool install` (pinned to the dependency versions the release was
tested with, `constraints.txt`), installs the extension with `code --install-extension`, and runs
`hitlcad doctor`.

**Windows** (PowerShell, in the unpacked folder):

```powershell
uv tool install --force --python (Get-Content python-version) --constraints constraints.txt (Get-Item hitlcad-*.whl)
code --install-extension (Get-Item hitlcad-*.vsix) --force
hitlcad doctor
```

### Check it

```console
$ hitlcad doctor
[ok ] backend build123d: build123d importable
[ok ] backend blender: Blender 4.5.14
[ok ] preview renderer (Blender Workbench)
[info] coding agents reach hitlcad's tools at http://127.0.0.1:8000/mcp
```

A backend that is missing only affects designs that use it. If `hitlcad` is not found, run
`uv tool update-shell` and open a new terminal. If Blender is installed but not on PATH, set
`HITLCAD_BLENDER_BINARY` to its executable.

### Remote workspaces

The extension runs where the workspace is. With Remote-SSH, WSL or a Dev Container, run the
installer in a terminal of that remote window: there `code` installs the extension on the
remote side, next to the tool. The panel reaches the server through VS Code's port forwarding.

## Set up a project

1. In VS Code run **hitlcad: New Design Project** and pick a folder (or run
   `hitlcad init ~/designs/my-project` and open it). It writes:

   | File | What it does |
   | --- | --- |
   | `designs/` | your designs, one folder each |
   | `.mcp.json` | gives Claude Code hitlcad's MCP tools |
   | `.claude/settings.json` | pre-approves those tools |
   | `.vscode/settings.json` | points VS Code's Python at the interpreter hitlcad builds with, so part files resolve `build123d` |
   | `CLAUDE.md`, `AGENTS.md` | point Claude Code and Codex at hitlcad's skills and rules |
   | `.claude/skills/`, `.agents/skills/` | the skills: `hitlcad` (how to work with designs) and `hitlcad-<backend>` (the code contract of each backend) |
   | `.gitignore` | |

2. hitlcad asks whether your agents may see what you select and pin in the panel. Allowing it
   adds a prompt hook to `.claude/settings.json` and `.codex/hooks.json` that puts your
   selection and open pins in your next message. For Codex it also adds hitlcad's MCP server and
   trusts the folder in `~/.codex/config.toml`, so Codex loads the hook; approve the hook once in
   Codex with `/hooks`. `hitlcad init --hooks --codex` allows both without asking.

3. Run **hitlcad: New Design**, name it and pick a backend, or ask your agent for one.

In a project made before, or one that lacks the setup, run **hitlcad: Set Up Claude Code and
Codex for This Project** (the extension offers it). To connect an agent by hand, `hitlcad connect`
prints the commands for both.

## Configuration

### VS Code settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `hitlcad.serverUrl` | `http://127.0.0.1:8000` | Where the extension reaches the server, and where it starts one |
| `hitlcad.autoStart` | `true` | Start the server when it is not running |
| `hitlcad.serverCommand` | `["hitlcad","serve"]` | The command that starts it |
| `hitlcad.serverCwd` | | Its working directory; a source checkout defaults to the workspace folder whose `pyproject.toml` is hitlcad |
| `hitlcad.autoOpenPanel` | `open` | What to do when a design is rebuilt and its panel is not on screen |

### Server environment

Environment variables (or a `.env` file), prefix `HITLCAD_`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `HITLCAD_BACKENDS` | `["build123d","blender"]` | Enabled CAD backends (plugins via entry points) |
| `HITLCAD_DATA_DIR` | `~/.hitlcad` | Design event logs and the full revision history |
| `HITLCAD_BLENDER_BINARY` | `blender` | Blender executable |
| `HITLCAD_BUILD_TIMEOUT_S` | `180` | Deadline of each build process |
| `HITLCAD_BUILD_WORKERS` | `4` | Part files built in parallel child processes |
| `HITLCAD_MESH_TOLERANCE_MM` | `0.01` | Chordal deviation of exported meshes |
| `HITLCAD_VERIFY_POSITION_MM` / `HITLCAD_VERIFY_ANGLE_DEG` | `0.01` / `0.05` | Default tolerances of every check; a design's `verification` overrides them |
| `HITLCAD_VERIFY_MOTION_SAMPLES` | `24` | Positions sampled per range of motion |
| `HITLCAD_HOST` / `HITLCAD_PORT` | `127.0.0.1` / `8000` | Where `hitlcad serve` listens |
| `HITLCAD_PUBLIC_URL` | `http://HOST:PORT` | Base URL that agents and the hook use to reach the server; the server also answers requests addressed to its host |

## Troubleshooting

- **The panel stays empty or says the server is unreachable.** Run **hitlcad: Show Server Logs**.
  If port 8000 is taken, set `hitlcad.serverUrl` to another port; the extension starts the server
  there.
- **The agent has no hitlcad tools.** Open the project folder itself (the one with `.mcp.json`),
  not a parent, and restart the agent's session. For Codex, check `~/.codex/config.toml` or run
  `hitlcad connect`.
- **Codex ignores your selection and pins.** Approve the hook with `/hooks` in Codex.
- **Part files show import errors in the editor.** Run `hitlcad init .` in the project folder; it
  points `.vscode/settings.json` at the installed tool's Python again.

## Update or remove

Install a newer release the same way; both commands replace the old version. Then run **hitlcad:
Set Up Claude Code and Codex for This Project** (or `hitlcad init`) in each project to bring its
skills and hooks up to date.

To remove: `uv tool uninstall hitlcad`, and uninstall "hitlcad" in VS Code's Extensions view.
Designs stay in your project folders and their history in `~/.hitlcad`.

## Safety

Model code runs locally in a child process with resource limits and a timeout, but **without
filesystem or network isolation**. Your own coding agent writes it, and that agent can already run
code on your machine. The server listens on `127.0.0.1`, answers only requests addressed to this
machine or to `HITLCAD_PUBLIC_URL` (so a web page cannot reach it by rebinding its own domain), and
has no authentication. Keep it single-user and local.
