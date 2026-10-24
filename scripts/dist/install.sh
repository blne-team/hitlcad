#!/usr/bin/env bash
# Install hitlcad from this folder: the hitlcad tool (with uv) and the VS Code extension.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

need() {
  command -v "$1" >/dev/null || { echo "hitlcad needs $2" >&2; exit 1; }
}
need uv "uv: https://docs.astral.sh/uv/getting-started/installation/"
need code "VS Code's 'code' command: in VS Code run 'Shell Command: Install code command in PATH'"

wheel=("$here"/hitlcad-*.whl)
vsix=("$here"/hitlcad-*.vsix)

echo "==> hitlcad tool"
uv tool install --force --python "$(cat "$here/python-version")" --constraints "$here/constraints.txt" "${wheel[0]}"
echo "==> VS Code extension"
code --install-extension "${vsix[0]}" --force

echo "==> checking backends"
bin="$(uv tool dir --bin)"
if ! "$bin/hitlcad" doctor; then
  echo "Some backends are missing (above). Designs that use them build once they are installed; the rest work now."
fi
case ":$PATH:" in
  *":$bin:"*) ;;
  *) echo "Note: $bin is not on PATH. Run 'uv tool update-shell' and open a new terminal." ;;
esac
echo
echo "Done. In VS Code run 'hitlcad: New Design Project' (Command Palette) to start."
