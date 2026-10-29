"""How a design project's coding agents reach hitlcad, each piece set up only with the human's permission:

- hooks (``hooks``): hitlcad's hook (`hitlcad.hook`) around every message the human sends, adding what they
  selected and pinned in the panel, and around every tool call that can write a design's files, so that what the
  agent writes is merged as its own and it hears what others changed meanwhile. It
  runs this machine's interpreter, so it goes where only this machine reads it: Claude Code's
  `.claude/settings.local.json` in the project (its per-user settings, kept out of the repository) and Codex's
  user `hooks.json`, as Codex has no per-user file for one project (in a folder without designs the hook adds
  nothing). Codex asks the human to approve the hook once (`/hooks`);
- Codex's user config (``codex``): hitlcad's MCP server in `config.toml`.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import Field

from hitlcad.hook import SERVER_WAIT_S
from hitlcad.kernel.model import Model

CLAUDE_SETTINGS = ".claude/settings.json"
CLAUDE_HOOKS = ".claude/settings.local.json"
HOOK_TIMEOUT_S = SERVER_WAIT_S + 2  # what the agents allow the hook: its wait for the server, and Python starting
#: The events hitlcad's hook handles in each agent, with the tools it is run around (None: every prompt and every
#: turn's end): those that can write a design's files.
EVENTS: dict[str, dict[str, str | None]] = {
    "claude": dict.fromkeys(
        ["PreToolUse", "PostToolUse", "PostToolUseFailure"], "Write|Edit|MultiEdit|NotebookEdit|Bash"
    )
    | {"UserPromptSubmit": None, "Stop": None},
    "codex": dict.fromkeys(["PreToolUse", "PostToolUse"], "Bash|apply_patch")
    | {"UserPromptSubmit": None, "Stop": None},
}


class AgentSetup(Model):
    """Which of the permission-gated pieces a project has, and the files they go in."""

    hooks: bool
    codex: bool
    claude_hooks: str = Field(description="Claude Code's file for the hook, in the project")
    codex_hooks: str = Field(description="Codex's user file for the hook")
    codex_config: str = Field(description="Codex's user config.toml, for the MCP server")


def hook_argv(python: str, agent: str) -> list[str]:
    """hitlcad's hook for one agent. Runs hitlcad's own interpreter, so it does not depend on PATH."""
    return [python, "-m", "hitlcad.hook", agent]


def command_line(argv: list[str]) -> str:
    """``argv`` quoted for the shell that runs an agent's hook command line: cmd.exe on Windows, sh elsewhere."""
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def hook_handler(python: str, agent: str) -> dict[str, Any]:
    """The hook's entry in the agent's config. Claude Code starts it from its argv with no shell in between;
    Codex takes only a command line."""
    argv = hook_argv(python, agent)
    if agent == "claude":
        return {"type": "command", "command": argv[0], "args": argv[1:], "timeout": HOOK_TIMEOUT_S}
    return {"type": "command", "command": command_line(argv), "timeout": HOOK_TIMEOUT_S}


def _is_ours(handler: dict[str, Any]) -> bool:
    words = " ".join([handler.get("command", ""), *handler.get("args", [])])
    return "hitlcad.hook" in words or "hitlcad.cli" in words and "context" in words  # the prompt-only hook it replaces


def hooks(config: dict[str, Any], agent: str, wanted: dict[str, Any]) -> bool:
    """Put hitlcad's hook on each event it handles in a Claude Code or Codex hooks config, around the tools it
    watches, in place of any of its own already there. Returns True when ``config`` was changed."""
    changed = False
    events = config.setdefault("hooks", {})
    for event, tools in EVENTS[agent].items():
        group = {"hooks": [wanted]} | ({"matcher": tools} if tools else {})
        groups = events.setdefault(event, [])
        if [g for g in groups if any(map(_is_ours, g.get("hooks", [])))] == [group]:
            continue
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not _is_ours(h)]
        groups[:] = [g for g in groups if g["hooks"]] + [group]
        changed = True
    return changed


def json_file(file: Path, update: Callable[[dict[str, Any]], bool]) -> bool:
    """Apply ``update`` to the JSON object in ``file`` (created if missing); write it if changed."""
    config = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    if not update(config):
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return True


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def codex_config_file() -> Path:
    return codex_home() / "config.toml"


def codex_hooks_file() -> Path:
    return codex_home() / "hooks.json"


def codex_server(mcp_url: str) -> dict[str, str]:
    """hitlcad's table under ``[mcp_servers]`` in Codex's config.toml: its tools run without asking each time."""
    return {"url": mcp_url, "default_tools_approval_mode": "approve"}


def codex_config(mcp_url: str) -> bool:
    """Add hitlcad's MCP server to Codex's config.toml, keeping every other line as it is. Returns True
    when the file was changed."""
    file = codex_config_file()
    before = file.read_text(encoding="utf-8") if file.is_file() else ""
    doc = tomlkit.parse(before)
    servers = doc.setdefault("mcp_servers", tomlkit.table(is_super_table=True))
    servers.setdefault("hitlcad", tomlkit.table()).update(codex_server(mcp_url))
    after = tomlkit.dumps(doc)
    if after == before:
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(after, encoding="utf-8")
    return True


def status(root: Path, python: str) -> AgentSetup:
    """What the project has of the agent setup: its hooks only as this hitlcad writes them (`python`'s), so a project
    set up by another asks again."""

    def hooked(file: Path, agent: str) -> bool:
        config = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
        return not hooks(config, agent, hook_handler(python, agent))

    file = codex_config_file()
    doc = tomlkit.parse(file.read_text(encoding="utf-8")) if file.is_file() else tomlkit.document()
    return AgentSetup(hooks=hooked(root / CLAUDE_HOOKS, "claude") and hooked(codex_hooks_file(), "codex"),
                      codex="hitlcad" in doc.get("mcp_servers", {}), claude_hooks=CLAUDE_HOOKS,
                      codex_hooks=str(codex_hooks_file()), codex_config=str(file))  # fmt: skip
