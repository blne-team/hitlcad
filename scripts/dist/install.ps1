# Install hitlcad from this folder: the hitlcad tool (with uv) and the VS Code extension.
# Run it with: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

function Need($exe, $what) {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { Write-Host "hitlcad needs $what"; exit 1 }
}
Need uv "uv: https://docs.astral.sh/uv/getting-started/installation/"
Need code "VS Code's 'code' command: in VS Code run 'Shell Command: Install code command in PATH'"

$wheel = (Get-ChildItem "$here\hitlcad-*.whl")[0].FullName
$vsix = (Get-ChildItem "$here\hitlcad-*.vsix")[0].FullName

Write-Host "==> hitlcad tool"
uv tool install --force --python (Get-Content "$here\python-version") --constraints "$here\constraints.txt" $wheel
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Write-Host "==> VS Code extension"
code --install-extension $vsix --force
if ($LASTEXITCODE) { exit $LASTEXITCODE }

Write-Host "==> checking backends"
$bin = uv tool dir --bin
& "$bin\hitlcad.exe" doctor
if ($LASTEXITCODE) {
  Write-Host "Some backends are missing (above). Designs that use them build once they are installed; the rest work now."
}
if (($env:PATH -split ";") -notcontains $bin) {
  Write-Host "Note: $bin is not on PATH. Run 'uv tool update-shell' and open a new terminal."
}
Write-Host ""
Write-Host "Done. In VS Code run 'hitlcad: New Design Project' (Command Palette) to start."
