"""Agent sessions, as their hooks report them (`hitlcad.hook`): what an agent's tools write is taken in as the session's
edit, and the agent hears, as it works, what others changed and where its edits crossed theirs.

While a tool that can write a design's files runs (a shell command, a file edit: from `PreToolUse` to its
`PostToolUse`, or `PostToolUseFailure`), the watcher leaves those designs to it; when it ends, what their folders
hold is taken in as the session's, each file from the version it was last written or read at: what the tool found on
disk (Claude Code edits no file changed since it was read). From its first tool that can write a design until its
turn ends (`Stop`), the design's folder stays as the agent's tools leave it, so no file it read changes under it and it
need not read one again: what others change merges into the design's files meanwhile, and its folder is written, and
the design rebuilt once, whole, when the turn ends (`DesignService.hold`). A tool that never ends (the human denied it)
ends with the turn, or with the session's next prompt. With each prompt and after each tool, the session is told what others (the human in the
panel, saves to the folder, other sessions) changed in the designs it works in since it was last told, and where its
edits crossed theirs: in one line each.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hitlcad.core.events import DesignState
from hitlcad.services.builds import BuildService
from hitlcad.services.context import ContextFeed
from hitlcad.services.designs import DesignService

#: A tool call not ended by then is taken as gone: the watcher takes its designs' folders in again.
TOOL_RUNS_S = 600
#: The files Codex's apply_patch names, in its patch.
PATCHED = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.MULTILINE)


@dataclass
class _Call:
    designs: list[str]  # those the tool can write
    started: float


class AgentSessions:
    def __init__(self, designs: DesignService, builds: BuildService, context: ContextFeed) -> None:
        self.designs = designs
        self.builds = builds
        self.context = context
        self._calls: dict[tuple[str, str], _Call] = {}  # (session, tool call) -> the tool running
        self._told: dict[tuple[str, str], int] = {}  # (session, design) -> the version it was last told about

    def busy(self, design_id: str) -> bool:
        """Whether an agent's tool that can write the design's files is running: what it writes is its to take in."""
        now = time.monotonic()
        return any(design_id in c.designs and now - c.started < TOOL_RUNS_S for c in self._calls.values())

    async def hook(self, agent: str, event: dict[str, Any]) -> str:
        """What the session's hook adds for `event`, as the agent gave it to its hook."""
        session, cwd = str(event.get("session_id", "")), str(event.get("cwd") or ".")
        origin, call = f"agent:{agent}:{session}", (session, str(event.get("tool_use_id", "")))
        match event.get("hook_event_name"):
            case "UserPromptSubmit":  # the session's last turn is over, if its Stop was not heard
                await self._end_turn(session)
                said = (self.context.deliver(cwd, agent, session), self._news(session, origin, cwd))
                return "\n\n".join(t for t in said if t)
            case "PreToolUse":
                touched = self._touched(event, cwd)
                for design_id in touched:
                    self.designs.hold(design_id, session)
                self._calls[call] = _Call(touched, time.monotonic())
            case "PostToolUse" | "PostToolUseFailure":  # a failed command may have written too
                started = self._calls.pop(call, None)  # none when the server came up meanwhile
                for design_id in started.designs if started else self._touched(event, cwd):
                    await self.designs.take_in(design_id, origin)
                return self._news(session, origin, cwd)
            case "Stop":
                await self._end_turn(session)
        return ""

    async def _end_turn(self, session: str) -> None:
        """The session's turn ended: a tool that never did (the human denied it) ends with it, whatever was saved
        meanwhile taken in; the designs it held get their folders written and build."""
        for gone in [k for k in self._calls if k[0] == session]:
            for design_id in self._calls.pop(gone).designs:
                await self.designs.take_in(design_id)
        for design_id in self.designs.release(session):
            self.builds.schedule_build(design_id)

    def _touched(self, event: dict[str, Any], cwd: str) -> list[str]:
        """The designs a tool can write: those of the files it names, or every design under the working directory."""
        given = event.get("tool_input") or {}
        named = [given[k] for k in ("file_path", "notebook_path") if isinstance(given.get(k), str)]
        if event.get("tool_name") == "apply_patch":
            named = [a or b for a, b in PATCHED.findall(str(given.get("command", "")))]
        if not named:
            return [s.id for s in self.designs.under(cwd)]
        owners = {self.designs.owner((Path(cwd) / name).expanduser().resolve()) for name in named}
        return sorted(o for o in owners if o is not None)

    def _news(self, session: str, origin: str, cwd: str) -> str:
        """What others changed in the designs the session works in since it was last told, and where its edits
        crossed theirs. A session is first told of what happens after it is first seen."""
        lines = []
        for state in self.designs.under(cwd):
            told = self._told.get((session, state.id))
            self._told[session, state.id] = state.version
            if told is not None:
                lines += _since(state, told, origin)
        if not lines:
            return ""
        return ("Meanwhile, in the hitlcad designs you work in (already merged with your edits: read a file again only "
                "where what changed matters to your work):\n" + "\n".join(lines))  # fmt: skip


def _since(state: DesignState, told: int, origin: str) -> list[str]:
    where = f"{state.name!r} ({state.path})"
    changes = [c for c in state.changes if c.version > told and c.origin != origin and c.files]
    lines = []
    for c in changes:
        lines.append(
            f"- {where}: {_who(c.origin)} changed {', '.join(c.files)}" + (f": {c.summary}" if c.summary else "")
        )
        lines += [
            f"    {line.path} line {line.line}: " + (f"`{line.now}`" if line.now else "removed") for line in c.lines
        ]
        lines += [f"    and {c.more} more lines"] if c.more else []
    for crossed in (x for x in state.crossed if x.version > told and origin in (x.origin, *x.others)):
        others = ", ".join(_who(o) for o in {crossed.origin, *crossed.others} - {origin}) or "another edit"
        lines += [f"- {where}: {c.path} line {c.line} was changed by you and {others} at once; the later edit was "
                  f"kept, and it reads `{c.now}` now. Make it what it should be." for c in crossed.crossed]  # fmt: skip
    return lines


def _who(origin: str) -> str:
    if origin == "panel":
        return "the human, in the panel,"
    if origin.startswith("agent:"):
        return "another agent session"
    return "a save to the folder" if origin == "disk" else "hitlcad"
