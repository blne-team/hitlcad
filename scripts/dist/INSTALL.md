# Installing hitlcad

hitlcad is two pieces, both in this folder:

- `hitlcad-<version>-py3-none-any.whl`: the hitlcad tool (server, MCP tools for Claude Code and
  Codex, and the 3D panel).
- `hitlcad-<version>.vsix`: the VS Code extension. It starts the tool when you open a design
  project.

## You need

- [VS Code](https://code.visualstudio.com/) with the `code` command on PATH (Command Palette:
  **Shell Command: Install 'code' command in PATH**).
- [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs hitlcad's default
  Python (`python-version`; 3.11 to 3.13 are supported) by itself.
- [Blender 4.5 LTS](https://www.blender.org/download/lts/) on PATH for Blender designs and the
  preview renders. build123d designs work without it.
- Claude Code or Codex in VS Code, to do the modelling.

## Install

macOS / Linux:

```bash
./install.sh
```

Windows (PowerShell, in this folder):

```powershell
uv tool install --force --python (Get-Content python-version) --constraints constraints.txt (Get-Item hitlcad-*.whl)
code --install-extension (Get-Item hitlcad-*.vsix) --force
hitlcad doctor
```

`hitlcad doctor` lists which backends are ready. If `hitlcad` is not found, run
`uv tool update-shell` and open a new terminal.

## Start

1. In VS Code run **hitlcad: New Design Project** and pick a folder. hitlcad asks whether your
   agents may see what you select and pin; allowing it adds a prompt hook for Claude Code and
   Codex (and, for Codex, trusts the folder in `~/.codex/config.toml`). In Codex, approve the
   hook once with `/hooks`.
2. Run **hitlcad: New Design**, then ask Claude Code or Codex for a part.
3. Every save rebuilds; the design opens in a 3D tab, and its pins, parameters and checks are in
   the **hitlcad › Design** view (bottom panel; drag it anywhere).

## Update or remove

Install a newer folder the same way (both commands replace the old version). Then run
**hitlcad: Set Up Claude Code and Codex for This Project** in each project (or `hitlcad init`
there) to bring its agent skills and hooks up to date.
To remove: `uv tool uninstall hitlcad`, and uninstall "hitlcad" in VS Code's Extensions view.
Designs and their history stay in your project folders and `~/.hitlcad`.
