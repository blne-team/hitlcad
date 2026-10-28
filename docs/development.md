# Developing hitlcad

## Run it from source

Open this repository in VS Code and press F5:

- **Extension** opens a second window on `~/hitlcad-playground` with the extension loaded from
  source.
- **Extension + Server** also runs `hitlcad serve` under the Python debugger.

See `.vscode/launch.json`. The devcontainer has everything a build needs (uv, Node 20+, Blender).

```bash
uv sync --extra dev
uv run pytest -m "not geometry"         # quick checks without CAD builds; -n0 runs in this process
uv run pytest -m "not geometry or smoke" # quick checks plus one small CAD build
uv run pytest -m "not examples"         # includes geometry regressions, skips example/teaching builds
uv run pytest                          # full suite, including every example configuration, in parallel
uv run ruff check backend && uv run ruff format backend
uv run lint-imports                    # hitlcad's own plugins (builtin/) and the rest import each other one way only
(cd vscode-extension && npm test)      # extension unit tests
(cd vscode-extension && npm run lint)  # ESLint: TypeScript's rules, React's rules of hooks
(cd vscode-extension && npm run compile)  # extension and panel UI (ui/ → out/ui/)
uv run hitlcad build examples/handhold-fan --out build-out   # build and verify a design folder
```

Dependencies resolve with a two-week supply-chain cooldown (`[tool.uv] exclude-newer` in
`pyproject.toml`; `npm install --before=<date>` in `vscode-extension/` and `e2e/`). Move the date
forward deliberately when upgrading.

## Tests and CI

Use `-m "not geometry"` for quick feedback, then `-m "not examples"` when changing build or analysis code.
The `examples` tests rebuild README examples and installed teaching designs in every configuration; run the
full suite when changing those assets or before submitting. CI keeps running the full suite on Linux.
The `smoke` marker selects one small build; the enclosure's export and cache checks remain in the geometry suite.

`.github/workflows/ci.yml` runs on every push and pull request: ruff and ESLint; the full backend suite,
including real build123d and Blender builds of the test designs and of every design in `examples/`, on
Linux with Python 3.11; on Windows and macOS, all of it but the exhaustive cases marked `once` (other
configurations of the same designs, the largest example); on Python 3.12 and 3.13, the tests that build no
geometry and one build (`smoke`); the extension's tests on all three, and on Linux its panel UI's
typecheck and build and its packaging; and the end-to-end tests (`e2e/`). Playwright drives the real server and
the panel UI in Chromium, each page loaded as the extension loads it into a webview, including the
extension's sync between the 3D editor tab and the Design view. To run them locally:

```bash
npm --prefix vscode-extension run compile
cd e2e && npm ci && npx playwright install chromium && npm test
```

## The README's screenshots

`docs/images/` is generated from `examples/` on the same server and harness as the end-to-end
tests, so the screenshots show what the current version does:

```bash
npm --prefix vscode-extension run compile
cd e2e && npm run screenshots
```

Each example's tour GIF is filmed a frame at a time, each frame once the view has drawn, so it plays
at the same pace however slowly software GL draws a large assembly.

The one picture of the whole VS Code window (`docs/images/vscode-extension.png`) is taken by hand.

## Build a release

```bash
scripts/package.sh        # or the VS Code task "release: package"
```

It builds the wheel and the extension with its panel UI, pins the dependencies to `uv.lock`
(`constraints.txt`) and the default Python to `.python-version`, and writes
`dist/hitlcad-<version>/` (with [INSTALL.md](../INSTALL.md) and `install.sh`) and
`dist/hitlcad-<version>.tar.gz`.

To publish, bump `version` in both `pyproject.toml` and `vscode-extension/package.json` (the
script refuses a mismatch), then push a matching tag:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

GitHub Actions (`.github/workflows/release.yml`) runs CI, which builds the release and installs it on
Linux, macOS and Windows, then publishes that same build as a GitHub release with the `.tar.gz`, the wheel
and the `.vsix` attached.

## Platforms

CI runs the backend tests (with Blender) and the extension's on Linux, macOS and Windows, and installs
the release package on each the way [INSTALL.md](../INSTALL.md) says, then builds an example with the
installed tool, after running the release's own installer (`install.sh`, `install.ps1`) against the
oldest VS Code the extension supports.

What differs per platform is kept to the process boundaries, each with one rule:

- **Text is UTF-8** across every boundary. Files name their encoding (ruff's PLW1514); hitlcad's own
  processes run in Python's UTF-8 mode (the extension starts the server with `PYTHONUTF8`, a
  build123d build runs `-X utf8`; Blender always does), and the `hitlcad` command, which an agent's
  hook or a terminal starts, sets its streams itself.
- **Builds** (`execution/executor.py`) are limited the same way everywhere: a deadline, and memory
  measured with psutil. Only how a process tree is held differs: a process group on POSIX, a job
  object on Windows.
- **Agent hooks** are started without a shell where the agent allows it (Claude Code's `args`), and
  otherwise quoted for the shell that runs them (`agents.command_line`: cmd.exe on Windows).
