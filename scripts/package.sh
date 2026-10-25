#!/usr/bin/env bash
# Build what someone needs to install hitlcad without this repository or F5:
#
#   dist/hitlcad-<version>/
#     hitlcad-<version>-py3-none-any.whl   the hitlcad tool: server, CLI and MCP tools
#     hitlcad-<version>.vsix               the VS Code extension, with the panel UI
#     constraints.txt                      the dependency versions tested here (from uv.lock)
#     python-version                       the default Python (.python-version) the tool is installed on
#     install.sh, install.ps1, INSTALL.md  how to install it
#   dist/hitlcad-<version>.tar.gz          the same folder, to share
#
# Usage: scripts/package.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

version="$(uv version --short)"
ext_version="$(node -p "require('./vscode-extension/package.json').version")"
if [[ "$version" != "$ext_version" ]]; then
  echo "version mismatch: pyproject.toml is $version, vscode-extension/package.json is $ext_version" >&2
  exit 1
fi

name="hitlcad-$version"
out="$root/dist/$name"
rm -rf "$out" "$root/dist/$name.tar.gz"
mkdir -p "$out"

echo "==> hitlcad tool (wheel)"
uv build --wheel --out-dir "$out"
rm -f "$out/.gitignore" # uv build marks its output folder ignored; not part of the release
uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt --output-file "$out/constraints.txt" >/dev/null
cp .python-version "$out/python-version"

echo "==> VS Code extension and panel UI"
npm --prefix vscode-extension ci --no-audit --no-fund
npm --prefix vscode-extension run package -- --out "$out/$name.vsix"

cp scripts/dist/install.sh scripts/dist/install.ps1 INSTALL.md "$out/"
chmod +x "$out/install.sh"
tar -czf "$root/dist/$name.tar.gz" -C "$root/dist" "$name"

echo
echo "Built $out"
ls -1 "$out"
echo "Share dist/$name.tar.gz; recipients unpack it and run ./install.sh (see INSTALL.md)."
