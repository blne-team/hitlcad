"""How a design project's coding agents reach hitlcad, each piece set up only with the human's permission:

- prompt hooks (``hooks``): a UserPromptSubmit hook that adds what the human selected and pinned in the
  panel to every message they send. It runs this machine's interpreter, so it goes where only this
  machine reads it: Claude Code's `.claude/settings.local.json` in the project (its per-user settings,
  kept out of the repository) and Codex's user `hooks.json`, as Codex has no per-user file for one
  project (in a folder without designs the hook adds nothing). Codex asks the human to approve the
  hook once (`/hooks`);
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

from hitlcad.core.types import Model

CLAUDE_SETTINGS = ".claude/settings.json"
CLAUDE_HOOKS = ".claude/settings.local.json"
SERVER_WAIT_S = 3  # how long the hook waits for the server before it adds nothing
HOOK_TIMEOUT_S = SERVER_WAIT_S + 2  # what the agents allow the hook: that wait, and Python starting


class AgentSetup(Model):
    """Which of the permission-gated pieces a project has."""

    hooks: bool
    codex: bool


def hook_argv(python: str, agent: str, server_url: str) -> list[str]:
    """The agents' UserPromptSubmit hook: adds what the human points at in the panel to each prompt. Runs
    hitlcad's own interpreter, so it does not depend on PATH."""
    return [python, "-m", "hitlcad.cli", "context", "--hook", agent, "--server", server_url]


def command_line(argv: list[str]) -> str:
    """``argv`` quoted for the shell that runs an agent's hook command line: cmd.exe on Windows, sh elsewhere."""
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def hook_handler(python: str, agent: str, server_url: str) -> dict[str, Any]:
    """The hook's entry in the agent's config. Claude Code starts it from its argv with no shell in between;
    Codex takes only a command line."""
    argv = hook_argv(python, agent, server_url)
    if agent == "claude":
        return {"type": "command", "command": argv[0], "args": argv[1:], "timeout": HOOK_TIMEOUT_S}
    return {"type": "command", "command": command_line(argv), "timeout": HOOK_TIMEOUT_S}


def _is_ours(handler: dict[str, Any]) -> bool:
    words = " ".join([handler.get("command", ""), *handler.get("args", [])])
    return "hitlcad.cli" in words and "context" in words


def _handlers(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [h for g in config.get("hooks", {}).get("UserPromptSubmit", []) for h in g.get("hooks", [])]


def prompt_hook(config: dict[str, Any], wanted: dict[str, Any]) -> bool:
    """Add hitlcad's UserPromptSubmit hook to a Claude Code or Codex hooks config, or bring it up to
    date. Returns True when ``config`` was changed."""
    for handler in filter(_is_ours, _handlers(config)):
        if handler == wanted:
            return False
        handler.clear()
        handler.update(wanted)
        return True
    groups = config.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    groups.append({"hooks": [wanted]})
    return True


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


def status(root: Path) -> AgentSetup:
    def hooked(file: Path) -> bool:
        return file.is_file() and any(map(_is_ours, _handlers(json.loads(file.read_text(encoding="utf-8")))))

    file = codex_config_file()
    doc = tomlkit.parse(file.read_text(encoding="utf-8")) if file.is_file() else tomlkit.document()
    return AgentSetup(hooks=hooked(root / CLAUDE_HOOKS) and hooked(codex_hooks_file()),
                      codex="hitlcad" in doc.get("mcp_servers", {}))  # fmt: skip
