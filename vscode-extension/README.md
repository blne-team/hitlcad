# hitlcad for VS Code

Design parametric CAD assemblies by chatting with Claude Code or Codex in VS Code. Each design is a normal folder: `design.json` declares the parts, how they connect and what to buy; `interfaces.py`, `parts/` and `assembly.py` build and place them. The agents edit these files with their own tools. Every save rebuilds what changed and verifies every fit, fastener, joint and requirement, and the hitlcad panel beside the editor shows the 3D assembly, the results, the structure, the bill of materials, pins and parameters for your review.

hitlcad is installed as a command-line tool. Its source code never needs to be in your workspace.

## Install

Unpack the hitlcad release (`hitlcad-<version>.tar.gz`) and run `./install.sh`, or follow its `INSTALL.md` (Windows too). It installs the hitlcad tool with [uv](https://docs.astral.sh/uv/) and this extension, then runs `hitlcad doctor`, which checks the backends, the renderer and the data folder (`~/.hitlcad`). In a dev container or Remote SSH window, install there: the extension runs where your files and the hitlcad server are.

## Start a design project

1. Open a new window and run **hitlcad: New Design Project** from the Command Palette, the Explorer welcome view, or the *Get started* walkthrough.
2. Choose an empty folder, or let hitlcad create a subfolder. hitlcad adds:
   - `designs/` for your designs
   - `.mcp.json`, which connects Claude Code
   - `CLAUDE.md` and `AGENTS.md`, which point the agents at hitlcad's skills
   - `.claude/skills/` and `.agents/skills/`, hitlcad's skills for Claude Code and Codex: how to work with designs, and the code contract of each backend
   - `.gitignore`
   - if you allow it (hitlcad asks first): a hook that adds your selection and open pins to each message you send, and around the agent's file edits and shell commands merges its edits with yours and tells it what you changed, in `.claude/settings.local.json` (your own Claude Code settings, not committed) and in Codex's user `~/.codex/hooks.json` (it adds nothing in folders without designs), and for Codex, hitlcad's MCP server in `~/.codex/config.toml`. Codex then asks you once to approve the hook (`/hooks`).

   Existing files are kept, except hitlcad's own `hitlcad*` skills, which are brought up to date.
3. Choose **Open Project**. The extension points VS Code's Python at the interpreter hitlcad builds with, so part files resolve `build123d`: through the Python extension, which keeps that choice for you in this workspace rather than in a committed file.

The extension starts the server (`hitlcad serve`) when it is needed. It stops it again when the window closes, if it started it.

## Workflow

1. **hitlcad: New Design**. Give it a name ("Gearbox") and pick a backend: build123d for precise parts, Blender for organic shapes. The design folder appears under `designs/` with `design.json`, `interfaces.py`, `assembly.py` and `parts/`, and the hitlcad panel opens.
2. **Ask your agent.** Open the Claude Code or Codex panel and describe what you want: *"A 4:1 planetary gearbox for a NEMA 17, printed in PLA, with a 608 bearing on the output."* The agent declares the structure in `design.json` first (parts, connectors, mates, the screws and bearings to buy), checks it, then writes the code part by part, calling hitlcad's tools to build, verify and measure.
3. **Save to rebuild.** Every save builds a new revision; only part files whose inputs changed are rebuilt. The design's panel opens beside the editor if it isn't showing (`hitlcad.autoOpenPanel`). **hitlcad: Rebuild Design** builds on demand. The latest successful build's files (the assembly's `model.step` and `mesh.stl`, each part's STEP and STL in its own frame under `parts/`, the bill of materials, `preview.png`) are in the design's git-ignored `build/` folder.
4. **Review in the panel.** The design's tab shows the 3D assembly; its Pins, Structure, Parameters, Changes, Results, Print, BOM and Brief are in the **hitlcad › Design** view (bottom panel by default; drag it to either sidebar like any VS Code view). The view follows the design tab you are looking at.
   - **Results**: what every build verified, by analysis: mates and fits, relations (gears), fastener sets (seating, clearance, thread engagement), collisions (also through each mate's range of motion), manufacturability per process, requirements.
   - **Structure**: the parts and components, and the mates, relations and fastener sets between them, coloured by their results. Click a part to select it.
   - **Parameters**: tune them with sliders (written to `design.json` and rebuilt), and switch between configurations, the design's variants. **Undo** and **Redo** (↶ ↷ in the design's tab, or Ctrl+Z and Ctrl+Shift+Z, ⌘ on macOS) take back your panel edits; what the agent wrote since stays.
   - **Compare** with an earlier revision (ghost overlay; the **Changes** tab lists parameters, size, results that changed and every file's diff).
   - **BOM**: the bill of materials, made and bought, with quantities.
   - Place pins with notes, then tell the agent *"address my pins"*. It resolves them when done.
5. **Point at things.** Hover a part in the panel to see its name; click it to select it, and click it again for the face, edge or vertex under the cursor (Alt-click goes there at once; a point on Blender parts); shift-click adds more, then say *"make the selected hole 0.5 mm larger"*. Selections are named by instance and connector, e.g. `lid.screws[2]`. In projects made with **New Design Project** (or `hitlcad init`), your selection and open pins go to Claude Code or Codex with your next message, through the project's hook; the panel shows *Sent to Claude Code at 14:02* or *Goes to your agent with your next message*. In Codex, approve hitlcad's hook (`/hooks`) once. **Copy for chat** (**hitlcad: Copy Selections and Pins for Chat**) copies the selections and pins of every design in the workspace, plus model-file selections, to paste anywhere.
6. **Prepare to print.** The **Print** tab beside the 3D view (with Results and Parameters) turns the view into the print view while it is open: each printed part once, lying as it prints, each on a bed of your printer's size, with the print plan: each printed part, how many, and which way up. Click the tab again, or another tab, to return to the assembly. It shades, seen through each part, what prints on nothing but support (red; amber where it shows once the product is assembled, so its support would scar it) and what lies on the bed (blue), as the build's checks found them. Each way names how much of its support, and of what lies on the bed, shows; the ways weigh support on what shows before support elsewhere. Pointing at one of a part's ways in its plan shows it lying that way, shaded the same, before you print it so. Hovering a part names it; clicking it (in the view or the list) frames it and shows its whole plan: whether it fits your bed, its settings, and the ways it could lie, best first, each one click from **Print this way**. The Design view's Print tab asks once for your printer: its build volume, nozzle, slicer, and the filament (and AMS slot) for each material you print. The project keeps it in its `hitlcad.toml`, and every build then checks each printed part against the bed, lying the way its process's `up` says it prints; the tab shows the same print plan as the print view, and the part chosen in either. Each part's settings (layer height, walls, infill, supports, brim) are recommended by hitlcad from what the build found, or decided by your agent, each with its reason. You need not tune them: ask your agent to change one. With the **For the slicer** output chosen, the tab offers `print.3mf` and `print-plan.md`: open the 3MF in Bambu Studio as a project and every part is there, as many as the design places, lying as it prints with its settings and filament, ready to arrange and slice.

### Viewing any 3D file

Click an `.stl`, `.step`/`.stp`, `.glb`/`.gltf`, `.3mf`, `.obj` or `.ply` file, such as the ones in a design's `build/` folder, and it opens in the hitlcad 3D Viewer. To see the raw file, use **Reopen Editor With… → Text Editor**. It is the same viewer as the panel: click to select a part, and again for its faces, edges and vertices on STEP files (points on meshes) and copy the selection for chat.

### Existing workspaces

**hitlcad: Set Up Claude Code and Codex for This Project** asks, then sets up the open folder as above: `.mcp.json`, the agents' hooks, and (if you allow it) hitlcad's entries in `~/.codex/config.toml`, leaving every other line of that file as it is. When a project lacks the setup, the extension offers it once; choose **Don't Ask Again** to stop that.

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
| hitlcad: Set Up Claude Code and Codex for This Project | Ask, then add the MCP configuration and hooks for both agents |
| hitlcad: Restart Server / Show Server Logs | Restart the local server (whoever started it), with hitlcad's code as it is now; read its log |

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `hitlcad.serverUrl` | `http://127.0.0.1:8000` | Where the hitlcad server listens. When the extension starts the server itself, it passes this host and port to it. |
| `hitlcad.autoStart` | `true` | Start the server when it is not running |
| `hitlcad.serverCommand` | `["hitlcad", "serve"]` | How to start the server. If `hitlcad` is not on PATH and a workspace folder is the hitlcad source checkout, `uv run hitlcad serve` is used there. |
| `hitlcad.serverCwd` | *(empty)* | Working directory for the server command; not needed for the installed tool |
| `hitlcad.autoOpenPanel` | `open` | When a design is rebuilt (by you or an agent) and its panel isn't showing: `open` it beside the editor, `notify`, or `off` |
| `hitlcad.selectionHint` | `true` | Show the hint on how to select along the bottom of a 3D view; its × turns it off |

## Remote and dev containers

The panel reaches the hitlcad server through `vscode.env.asExternalUri`, so desktop VS Code forwards the server port automatically in dev containers, Remote SSH and Codespaces.

## Building the extension

```bash
npm install
npm test           # unit tests for the pure helpers
npm run package    # → hitlcad-<version>.vsix (scripts/package.sh in the repo builds the full release)
```
