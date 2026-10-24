# Developing hitlcad

## Run it from source

Open this repository in VS Code and press F5:

- **Extension** opens a second window on `~/hitlcad-playground` with the extension loaded from
  source.
- **Extension + Server** also runs `hitlcad serve` under the Python debugger.

See `.vscode/launch.json`. The devcontainer has everything a build needs (uv, Node 20+, Blender).

```bash
uv sync --extra dev
uv run pytest                          # all tests; -m "not geometry" skips real-kernel tests
uv run ruff check backend && uv run ruff format backend
(cd vscode-extension && npm test)      # extension unit tests
(cd vscode-extension && npm run lint)  # ESLint: TypeScript's rules, React's rules of hooks
(cd vscode-extension && npm run compile)  # extension and panel UI (ui/ → out/ui/)
uv run hitlcad build examples/handhold-fan --out build-out   # build and verify a design folder
```

Dependencies resolve with a two-week supply-chain cooldown (`[tool.uv] exclude-newer` in
`pyproject.toml`; `npm install --before=<date>` in `vscode-extension/` and `e2e/`). Move the date
forward deliberately when upgrading.

## Tests and CI

`.github/workflows/ci.yml` runs on every push and pull request: ruff and ESLint; the full backend suite,
including real build123d and Blender builds of the test designs and of every design in
`examples/`, on Python 3.11, 3.12 and 3.13; the extension's tests, its panel UI's typecheck and
build, and its packaging; and the end-to-end tests (`e2e/`). Playwright drives the real server and
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

GitHub Actions (`.github/workflows/release.yml`) runs CI, builds the release and publishes it as a
GitHub release with the `.tar.gz`, the wheel and the `.vsix` attached.
