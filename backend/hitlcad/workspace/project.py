"""Scaffold a design project: a clean workspace for designs, with coding agents pre-configured."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from hitlcad.agent import skills
from hitlcad.backends.base import CADBackend
from hitlcad.config import Settings
from hitlcad.workspace import agents

AGENT_GUIDE = """\
# Design project (hitlcad)

Designs live in `designs/<name>/`; the human reviews them in the hitlcad panel in VS Code.
Follow the `hitlcad` MCP instructions. Use the `hitlcad` skill for design work and
`hitlcad-<backend>` before editing model code; load references and companion skills as needed.
Skills are installed in `.claude/skills/` and `.agents/skills/` (`hitlcad init .` installs them).
Build, measure, export and render through hitlcad tools; other CAD skills serve as API reference.
"""

IGNORED = ["__pycache__/", agents.CLAUDE_HOOKS]  # the agents' hook names this machine's interpreter


def _claude_settings(config: dict[str, Any]) -> bool:
    """Pre-approve the project's hitlcad MCP server for Claude Code."""
    enabled = config.setdefault("enabledMcpjsonServers", [])
    if "hitlcad" in enabled:
        return False
    enabled.append("hitlcad")
    return True


def _gitignore(file: Path) -> bool:
    """Add the entries of ``IGNORED`` that ``file`` lacks. Returns True when changed."""
    lines = file.read_text(encoding="utf-8").splitlines() if file.is_file() else []
    missing = [entry for entry in IGNORED if entry not in lines]
    if not missing:
        return False
    header = [] if "# hitlcad" in lines else ["# hitlcad"]
    file.write_text("\n".join([*lines, *header, *missing]) + "\n", encoding="utf-8")
    return True


def init_project(
    path: Path, settings: Settings, python: str, backends: Iterable[CADBackend], *, hooks: bool, codex: bool
) -> list[str]:
    """Create the project skeleton without overwriting the human's files; `python` is the interpreter
    that builds models (hitlcad's own). hitlcad's entries in the agents' configs and its skills are
    kept current. `hooks` and `codex` are the human's permission for the agents' hooks and
    for Codex's user config (see `agents`). Returns the paths written/updated."""
    root = path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    designs = root / "designs"
    if not designs.exists():
        designs.mkdir()
        (designs / ".gitkeep").touch()
        written.append("designs/")

    def hook(agent: str) -> Callable[[dict[str, Any]], bool]:
        return lambda config: agents.hooks(config, agent, agents.hook_handler(python, agent, settings.base_url))

    def mcp_server(config: dict[str, Any]) -> bool:
        servers = config.setdefault("mcpServers", {})
        wanted = {"type": "http", "url": settings.mcp_url}
        if servers.get("hitlcad") == wanted:
            return False
        servers["hitlcad"] = wanted
        return True

    def shown(file: Path) -> str:
        """In the project: its path there; else from the home folder (Codex's user files)."""
        if file.is_relative_to(root):
            return file.relative_to(root).as_posix()
        return str(file).replace(str(Path.home()), "~", 1)

    files: dict[Path, Callable[[dict[str, Any]], bool]] = {
        root / ".mcp.json": mcp_server,
        root / agents.CLAUDE_SETTINGS: _claude_settings,
    }
    if hooks:
        files[root / agents.CLAUDE_HOOKS] = hook("claude")
        files[agents.codex_hooks_file()] = hook("codex")
    for file, update in files.items():
        if agents.json_file(file, update):
            written.append(shown(file))
    if codex and agents.codex_config(settings.mcp_url):
        written.append(shown(agents.codex_config_file()))

    for name in ("CLAUDE.md", "AGENTS.md"):
        target = root / name
        if not target.exists():
            target.write_text(AGENT_GUIDE, encoding="utf-8")
            written.append(name)

    written += skills.install(root, backends)

    if _gitignore(root / ".gitignore"):
        written.append(".gitignore")
    return written
