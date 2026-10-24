"""Scaffold a design project: a clean workspace for designs, with coding agents pre-configured."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from hitlcad import skills
from hitlcad.backends.base import CADBackend
from hitlcad.designs import agents

AGENT_GUIDE = """\
# Design project (hitlcad)

This workspace holds parametric CAD assemblies in `designs/<name>/` (`design.json`, `interfaces.py`,
`parts/`, `assembly.py`). The `hitlcad` MCP server builds, verifies and renders them; the human
reviews each build in the hitlcad panel in VS Code and talks to you here, in your own chat.

Use the `hitlcad` skill for any work on a design, and the `hitlcad-<backend>` skill before writing a
design's code. hitlcad installs both in `.claude/skills/` and `.agents/skills/`; if they are
missing, run `hitlcad init .` in this folder.

- Declare the structure in design.json before writing geometry, and check it with
  `build_design(path, scope="structure")`.
- Build, measure and render only through the hitlcad tools. Other CAD, Blender or bpy skills are
  API reference for part files, not a way to build or export.
- Never edit `build/`; it is regenerated from the design's files.
- When the human points at something ("this", "the selected …", pins), hitlcad's prompt hook has
  added it to their message, named by instance and connector. `explore_design(path)` repeats it.
"""

GITIGNORE = """\
# hitlcad
__pycache__/
"""


def _claude_settings(config: dict[str, Any]) -> bool:
    """Pre-approve the project's hitlcad MCP server for Claude Code."""
    enabled = config.setdefault("enabledMcpjsonServers", [])
    if "hitlcad" in enabled:
        return False
    enabled.append("hitlcad")
    return True


def _vscode_settings(file: Path, python: str) -> bool:
    """Point VS Code's Python extension at the interpreter hitlcad builds models with, so model.py
    resolves its imports (build123d) and no unrelated interpreter is tried. Returns True when changed."""
    try:
        settings = json.loads(file.read_text()) if file.is_file() else {}
    except json.JSONDecodeError as e:
        raise ValueError(
            f'{file} is not plain JSON ({e}); add "python.defaultInterpreterPath": "{python}" to it yourself'
        ) from None
    if settings.get("python.defaultInterpreterPath") == python:
        return False
    settings["python.defaultInterpreterPath"] = python
    file.parent.mkdir(exist_ok=True)
    file.write_text(json.dumps(settings, indent=2) + "\n")
    return True


def init_project(
    path: Path, server_url: str, python: str, backends: Iterable[CADBackend], *, hooks: bool, codex: bool
) -> list[str]:
    """Create the project skeleton without overwriting the human's files; `python` is the interpreter
    that builds models (hitlcad's own). hitlcad's entries in the agents' configs and its skills are
    kept current. `hooks` and `codex` are the human's permission for the agents' prompt hooks and
    for Codex's user config (see `agents`). Returns the paths written/updated."""
    root = path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    designs = root / "designs"
    if not designs.exists():
        designs.mkdir()
        (designs / ".gitkeep").touch()
        written.append("designs/")

    def hook(agent: str) -> str:
        return agents.hook_command(python, agent, server_url)

    def mcp_server(config: dict[str, Any]) -> bool:
        servers = config.setdefault("mcpServers", {})
        wanted = {"type": "http", "url": f"{server_url}/mcp"}
        if servers.get("hitlcad") == wanted:
            return False
        servers["hitlcad"] = wanted
        return True

    files: dict[str, list[Callable[[dict[str, Any]], bool]]] = {
        ".mcp.json": [mcp_server],
        agents.CLAUDE_SETTINGS: [_claude_settings],
    }
    if hooks:
        files[agents.CLAUDE_SETTINGS].append(lambda c: agents.prompt_hook(c, hook("claude")))
        files[agents.CODEX_HOOKS] = [lambda c: agents.prompt_hook(c, hook("codex"))]
    for name, updates in files.items():
        if agents.json_file(root, name, lambda c, updates=updates: any([u(c) for u in updates])):
            written.append(name)
    if codex and agents.codex_config(root, server_url):
        written.append(str(agents.codex_config_file()).replace(str(Path.home()), "~", 1))

    if _vscode_settings(root / ".vscode" / "settings.json", python):
        written.append(".vscode/settings.json")

    for name in ("CLAUDE.md", "AGENTS.md"):
        target = root / name
        if not target.exists():
            target.write_text(AGENT_GUIDE)
            written.append(name)

    written += skills.install(root, backends)

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE)
        written.append(".gitignore")
    return written
