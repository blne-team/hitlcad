# hitlcad

Parametric CAD assemblies for the coding agent you already use. Claude Code or Codex declares a
product's parts and how they connect, then writes the code that builds them in **build123d**
(exact B-rep, STEP) or **Blender** (meshes), in VS Code. hitlcad builds it, verifies every
connector, fit, gear mesh, fastener, collision, motion, process and requirement, and shows each
build in a 3D panel next to the code. Select faces, pin notes to the surface and tune parameters
there, then keep talking to the agent in its own chat: it sees what you pointed at.

hitlcad has no chat or agent of its own. It adds MCP tools to your agent and a review panel to
VS Code. See [docs/architecture.md](docs/architecture.md) for the architecture and
[docs/assembly-model.md](docs/assembly-model.md) for the model of a design.

## Install

hitlcad ships as a folder with the hitlcad tool (a wheel), the VS Code extension (with the panel
UI) and an installer. Unpack `hitlcad-<version>.tar.gz` and run `./install.sh`; its
`INSTALL.md` covers prerequisites (VS Code, [uv](https://docs.astral.sh/uv/), optionally Blender
4.5 LTS) and Windows.

## Build a release

```bash
scripts/package.sh        # or the VS Code task "release: package"
```

It builds the wheel and the extension with its panel UI, pins the dependencies to `uv.lock`
(`constraints.txt`) and the default Python to `.python-version`, and writes
`dist/hitlcad-<version>/` and `dist/hitlcad-<version>.tar.gz`. Building needs Node 20+ and uv;
the devcontainer has both.

To publish, bump `version` in both `pyproject.toml` and `vscode-extension/package.json` (the
script refuses a mismatch), then push a matching tag:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

GitHub Actions (`.github/workflows/release.yml`) runs CI, builds the release and publishes it as a
GitHub release with the `.tar.gz`, the wheel and the `.vsix` attached.

## CI

`.github/workflows/ci.yml` runs on every push and pull request: ruff; the full backend suite,
including real build123d and Blender builds, on Python 3.11, 3.12 and 3.13; the extension's
tests, its panel UI's typecheck and build, and its packaging; and the end-to-end tests (`e2e/`):
Playwright drives the real server and the panel UI in Chromium, each page loaded as the extension
loads it into a webview, including the extension's sync between the 3D editor tab and the Design
view. To run them locally:

```bash
npm --prefix vscode-extension run compile
cd e2e && npm ci && npx playwright install chromium && npm test
```

## Use it

1. Run **hitlcad: New Design Project** in VS Code (or `hitlcad init ~/designs/my-project`) and
   open the folder. It contains `designs/` and the files that set up your agents:
   - `.mcp.json` gives Claude Code hitlcad's tools.
   - `.claude/settings.json` pre-approves them.
   - If you allow it (hitlcad asks): a prompt hook in `.claude/settings.json` and
     `.codex/hooks.json` that adds your panel selection and open pins to your next message in
     Claude Code and Codex, and for Codex, hitlcad's MCP server and trust for the folder in
     `~/.codex/config.toml`, so Codex loads the hook. `hitlcad init --hooks --codex` allows both
     without asking.
   - `.vscode/settings.json` points VS Code's Python at the interpreter hitlcad builds with, so
     part files resolve `build123d`.
   - `CLAUDE.md` and `AGENTS.md` point the agents at hitlcad's skills and its rules.
   - `.claude/skills/` and `.agents/skills/` hold hitlcad's skills for Claude Code and Codex:
     `hitlcad` (how to work with designs) and `hitlcad-<backend>` (the code contract per
     backend). Running `hitlcad init` again brings them up to date.
   - `.gitignore`.

   The extension starts `hitlcad serve` when needed.
2. Run **hitlcad: New Design**, name it and pick a backend, or ask your agent to create one. A
   design is an assembly, in a folder (see [docs/assembly-model.md](docs/assembly-model.md)):
   - `design.json`: the structure (parts, made or bought, with their connectors; the mates,
     relations and fastener sets between them; components, which are other designs used as
     sub-assemblies), parameters, configurations (variants), requirements and the brief;
   - `interfaces.py`: the mating values two parts share, computed once;
   - `parts/*.py`: the code that builds each part in its own frame, the way it is made;
   - `assembly.py`: where each instance sits.
3. Describe the product to Claude Code or Codex in their panel. They declare the structure
   first and check it, then write the code part by part, with standard screws, inserts and
   bearings from a catalog. They edit the files with their normal tools, so you review diffs as
   usual. **Every save rebuilds** what changed: each part file is cached by exactly the
   parameters, interface values and helpers it read. When a build finishes, the design's panel
   opens beside the editor (`hitlcad.autoOpenPanel`). The files of the latest successful build
   are in the design's git-ignored `build/` folder: the assembly (`model.step`, `mesh.stl`), each
   part in its own frame (`parts/<part>.step`, `.stl`), `preview.png` and the outputs the
   design asks for (bill of materials, 3MF, URDF, SDF).
4. Review in the panel. The 3D view is [three-cad-viewer](https://github.com/bernhard-42/three-cad-viewer):
   its toolbar has the instance tree, clipping planes, distance and property measurement, zebra
   stripes, explode and standard views. Pins, Structure, Parameters, Changes, Results, BOM and
   Brief are in the **hitlcad › Design** view, in the bottom panel by default; drag it to
   either sidebar like any VS Code view. It follows the design tab you are looking at.
   - **Select**: click a face, edge or vertex of an instance (B-rep parts) or a point (mesh
     parts): "make the selected hole 0.5 mm larger". Picks are named by instance and connector,
     e.g. `lid.screws[2]`. Shift-click adds more. The viewer's filter (top right) limits clicks
     to vertices, edges, faces or whole instances (Solid). While the viewer's distance or
     properties tool is on, clicks measure instead; **Place pins** turns it off.
   - **Motion**: a mate with a range of motion (a hinge, a slide) gets a slider over the 3D view
     that moves its moving side through the range the build checked for collisions.
   - **Place pins** with notes, then tell the agent "address my pins". It resolves them when
     done. Pins follow their instance and connector across rebuilds.
   - **Structure**: parts and components as a graph, with the mates, relations and fastener
     sets between them coloured by their results; click a part to select it. It also shows what
     your agent reads about the design.
   - **Parameters**: changes are written to `design.json` and rebuilt; switch configurations.
   - **Compare**: draw an earlier revision as a ghost; the **Changes** tab lists changed
     parameters, size and mass, results that changed and every file's diff.
   - **Results**: what every build verified, by analysis: structure, parts, connector features,
     mates and fits, relations, fastener sets, coverage, interference, motion, manufacturing
     (FDM, CNC milling, sheet metal, injection molding, per made part in its own frame) and
     requirements (size, mass, holes, walls, clearance, tolerance stacks), with renders and
     downloads.
   - **BOM**: made and bought parts with quantities, through components.
   - **Brief**: what the design is meant to be, as the agent wrote it.

Your selection and open pins go to Claude Code or Codex with your next message: the project's
prompt hook adds what changed since that chat was last told, and the panel shows whether it has
been sent. Codex then asks you once to approve the hook (`/hooks`); until then it skips it. **Copy for chat**
copies the selections and pins of every design in the workspace, to paste anywhere. In an existing
project, **hitlcad: Set Up Claude Code and Codex for This Project** asks again (the extension also
offers it when a project lacks the setup). `hitlcad connect` prints the manual commands for both agents.

Open any `.step`, `.stl`, `.glb`, `.3mf`, `.obj` or `.ply` with **Open in 3D Viewer**.

The full revision history lives in `~/.hitlcad` (`HITLCAD_DATA_DIR`). Design folders hold what
you would commit plus the git-ignored `build/`.

## Configuration

Environment variables (or `.env`), prefix `HITLCAD_`:

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
| `HITLCAD_PUBLIC_URL` | `http://HOST:PORT` | Base URL that agents and the hook use to reach the server |

## Develop

Open this repository in VS Code and press F5:

- **Extension** opens a second window on `~/hitlcad-playground` with the extension loaded from
  source.
- **Extension + Server** also runs `hitlcad serve` under the Python debugger.

See `.vscode/launch.json`.

```bash
uv sync --extra dev
uv run pytest                          # all tests; -m "not geometry" skips real-kernel tests
uv run ruff check backend && uv run ruff format backend
(cd vscode-extension && npm test)      # extension unit tests
(cd vscode-extension && npm run compile)  # extension and panel UI (ui/ → out/ui/)
uv run hitlcad build backend/tests/designs/gearbox --out build-out   # build and verify a design folder
```

Dependencies resolve with a two-week supply-chain cooldown (`[tool.uv] exclude-newer` in
`pyproject.toml`; `npm install --before=<date>` in `vscode-extension/` and `e2e/`). Move the date forward
deliberately when upgrading.

## Safety

Model code runs locally in a child process with resource limits and a timeout, but **no
filesystem or network isolation**. It is written by your own coding agent, which can already
run code on your machine. The server binds to `127.0.0.1` and
has no authentication. Keep it single-user and local until a container executor and auth are
added.
