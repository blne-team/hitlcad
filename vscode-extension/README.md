# hitlcad for VS Code

Design parametric CAD parts by chatting with Claude Code or Codex in VS Code. Each design is a normal folder holding `design.json` and `model.py`. The agents edit `model.py` with their own tools. Every save rebuilds the model, and the hitlcad panel beside the editor shows the 3D result, the brief, the checks, pins and parameters for your review.

hitlcad is installed as a command-line tool. Its source code never needs to be in your workspace.

## Install

Unpack the hitlcad release (`hitlcad-<version>.tar.gz`) and run `./install.sh`, or follow its `INSTALL.md` (Windows too). It installs the hitlcad tool with [uv](https://docs.astral.sh/uv/) and this extension, then runs `hitlcad doctor`, which checks the backends, the renderer and the data folder (`~/.hitlcad`). In a dev container or Remote SSH window, install there: the extension runs where your files and the hitlcad server are.

## Start a design project

1. Open a new window and run **hitlcad: New Design Project** from the Command Palette, the Explorer welcome view, or the *Get started* walkthrough.
2. Choose an empty folder, or let hitlcad create a subfolder. hitlcad adds:
   - `designs/` for your designs
   - `.mcp.json`, which connects Claude Code
   - `.vscode/settings.json`, which points VS Code's Python at the interpreter hitlcad builds with, so `model.py` resolves `build123d`
   - `CLAUDE.md` and `AGENTS.md`, which point the agents at hitlcad's skills
   - `.claude/skills/` and `.agents/skills/`, hitlcad's skills for Claude Code and Codex: how to work with designs, and the code contract of each backend
   - `.gitignore`
   - if you allow it (hitlcad asks first): a prompt hook in `.claude/settings.json` and `.codex/hooks.json` that adds your selection and open pins to each message you send, and for Codex, hitlcad's MCP server and trust for the folder in `~/.codex/config.toml`. Codex then asks you once to approve the hook (`/hooks`).

   Existing files are kept, except hitlcad's own `hitlcad*` skills, which are brought up to date.
3. Choose **Open Project**.

The extension starts the server (`hitlcad serve`) when it is needed. It stops it again when the window closes, if it started it.

## Workflow

1. **hitlcad: New Design**. Give it a name ("Wall hook") and pick a backend: build123d for precise parts, Blender for organic shapes. The design folder appears under `designs/` with its `design.json`, and the hitlcad panel opens. There is no model yet: your agent writes `model.py` in the next step.
2. **Ask your agent.** Open the Claude Code or Codex panel and describe what you want. For example: *"Design a wall hook for a 20 mm strap with two countersunk screw holes; write the brief first."* The agent edits `model.py` and `design.json` with its own tools and calls hitlcad's tools to build and inspect the result.
3. **Save to rebuild.** Every save of `model.py` or `design.json` builds a new revision, and the design's panel opens beside the editor if it isn't showing (`hitlcad.autoOpenPanel`). **hitlcad: Rebuild Design** builds on demand. The latest successful build's files (`model.step`, `mesh.stl`, `preview.png`) are in the design's git-ignored `build/` folder.
4. **Review in the panel.** The design's tab shows the 3D model; its Pins, Parameters, Changes, Checks and Brief are in the **hitlcad › Design** view (bottom panel by default; drag it to either sidebar like any VS Code view). The view follows the design tab you are looking at.
   - Orbit the model and read the checks. The viewer's toolbar has the part tree, clipping planes, distance and property measurement, zebra stripes, explode and standard views.
   - Place pins with notes, then tell the agent *"address my pins"*. It resolves them when done.
   - Change parameters with the sliders; the values are written to `design.json` and rebuilt.
   - **Compare** with an earlier revision (ghost overlay; the **Changes** tab lists parameters, size, checks and the `model.py` diff).
   - Read the brief the agent wrote.
5. **Point at things.** Click a face, edge or vertex in the panel (a point on Blender designs), or a whole part with the filter at the top right on **Solid**; shift-click adds more, then say *"make the selected hole 0.5 mm larger"*. In projects made with **New Design Project** (or `hitlcad init`), your selection and open pins go to Claude Code or Codex with your next message, through the project's prompt hook; the panel shows *Sent to Claude Code at 14:02* or *Goes to your agent with your next message*. In Codex, trust the project and then its hook (`/hooks`) once. **Copy for chat** (**hitlcad: Copy Selections and Pins for Chat**) copies the selections and pins of every design in the workspace, plus model-file selections, to paste anywhere.

### Viewing any 3D file

Click an `.stl`, `.step`/`.stp`, `.glb`/`.gltf`, `.3mf`, `.obj` or `.ply` file, such as the ones in a design's `build/` folder, and it opens in the hitlcad 3D Viewer. To see the raw file, use **Reopen Editor With… → Text Editor**. It is the same viewer as the panel: click to select faces, edges and vertices of STEP files (points on meshes) and copy the selection for chat.

### Existing workspaces

**hitlcad: Set Up Claude Code and Codex for This Project** asks, then sets up the open folder as above: `.mcp.json`, the prompt hooks, and (if you allow it) hitlcad's entries in `~/.codex/config.toml`, leaving every other line of that file as it is. When a project lacks the setup, the extension offers it once; choose **Don't Ask Again** to stop that.

## Commands

| Command | What it does |
| --- | --- |
| hitlcad: New Design Project | Set up a folder for designs and agent instructions |
| hitlcad: New Design | Create a design folder and open it with its panel |
| hitlcad: Open Design | Open the design containing the active file, or a folder you pick |
| hitlcad: Show Design Panel | Show the panel for the current design (the status bar item does the same) |
| hitlcad: Rebuild Design | Build the current design now |
| hitlcad: Open in 3D Viewer | View a model file |
| hitlcad: Copy Selections and Pins for Chat | Copy what you selected and pinned in every design, to paste |
| hitlcad: Set Up Claude Code and Codex for This Project | Ask, then add the MCP configuration and prompt hooks for both agents |
| hitlcad: Start Server / Show Server Logs | Manage the local server |

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `hitlcad.serverUrl` | `http://127.0.0.1:8000` | Where the hitlcad server listens. When the extension starts the server itself, it passes this host and port to it. |
| `hitlcad.autoStart` | `true` | Start the server when it is not running |
| `hitlcad.serverCommand` | `["hitlcad", "serve"]` | How to start the server. If `hitlcad` is not on PATH and a workspace folder is the hitlcad source checkout, `uv run hitlcad serve` is used there. |
| `hitlcad.serverCwd` | *(empty)* | Working directory for the server command; not needed for the installed tool |
| `hitlcad.autoOpenPanel` | `open` | When a design is rebuilt (by you or an agent) and its panel isn't showing: `open` it beside the editor, `notify`, or `off` |

## Remote and dev containers

The panel reaches the hitlcad server through `vscode.env.asExternalUri`, so desktop VS Code forwards the server port automatically in dev containers, Remote SSH and Codespaces.

## Building the extension

```bash
npm install
npm test           # unit tests for the pure helpers
npm run package    # → hitlcad-<version>.vsix (scripts/package.sh in the repo builds the full release)
```
