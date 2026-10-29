"""Agent sessions, as their hooks report them (`hitlcad.hook`): what an agent writes is merged from the version it
started from, and the agent hears, as it works, what others changed and where its edits crossed theirs.

A tool that can write a design's files (a shell command, a file edit) starts from the version each design is at when
it starts (`PreToolUse`); a file written whole from what the agent read of it (Claude Code's Write) starts from the
version it was read at. While a tool runs, the watcher leaves the designs it can write to it; when it ends
(`PostToolUse`), what the folders hold is taken in from those versions. With each prompt and after each tool, the
session is told what others (the human in the panel, saves to the folder, other sessions) changed in the designs it
works in since it was last told, and where its edits crossed theirs: in one line each.
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
WHOLE_FILE = {"Write"}  # tools that write a file whole, from what the agent read of it
READS = {"Read"}


@dataclass
class _Call:
    bases: dict[str, int | None]  # design -> the version what the tool writes starts from
    paths: dict[str, list[str]]  # design -> the files the tool names (none: any)
    started: float


class AgentSessions:
    def __init__(self, designs: DesignService, builds: BuildService, context: ContextFeed) -> None:
        self.designs = designs
        self.builds = builds
        self.context = context
        self._calls: dict[tuple[str, str], _Call] = {}  # (session, tool call) -> what it started from
        self._reads: dict[tuple[str, str, str], int] = {}  # (session, design, file) -> the version it was read at
        self._told: dict[tuple[str, str], int] = {}  # (session, design) -> the version it was last told about

    def busy(self, design_id: str) -> bool:
        """Whether an agent's tool that can write the design's files is running: what it writes is its to take in."""
        now = time.monotonic()
        return any(design_id in c.bases and now - c.started < TOOL_RUNS_S for c in self._calls.values())

    async def hook(self, agent: str, event: dict[str, Any]) -> str:
        """What the session's hook adds for `event`, as the agent gave it to its hook."""
        session, cwd = str(event.get("session_id", "")), str(event.get("cwd") or ".")
        origin = f"agent:{agent}:{session}"
        match event.get("hook_event_name"):
            case "UserPromptSubmit":
                said = (self.context.deliver(cwd, agent, session), self._news(session, origin, cwd))
                return "\n\n".join(t for t in said if t)
            case "PreToolUse":
                self._start(session, event, cwd)
            case "PostToolUse":
                await self._end(session, origin, event, cwd)
                return self._news(session, origin, cwd)
        return ""

    def _touched(self, event: dict[str, Any], cwd: str) -> dict[str, list[str]]:
        """The designs a tool can write, and the files of each it names (none: it may write any)."""
        given = event.get("tool_input") or {}
        named = [given[k] for k in ("file_path", "notebook_path") if isinstance(given.get(k), str)]
        if event.get("tool_name") == "apply_patch":
            named = [a or b for a, b in PATCHED.findall(str(given.get("command", "")))]
        if not named:
            return {s.id: [] for s in self.designs.under(cwd)}
        out: dict[str, list[str]] = {}
        for name in named:
            path = (Path(cwd) / name).expanduser().resolve()
            if (design_id := self.designs.owner(path)) is not None:
                out.setdefault(design_id, []).append(path.relative_to(self.designs.get(design_id).path).as_posix())
        return out

    def _start(self, session: str, event: dict[str, Any], cwd: str) -> None:
        touched = self._touched(event, cwd)
        bases: dict[str, int | None] = {}
        for design_id, paths in touched.items():
            bases[design_id] = self.designs.files(design_id).version
            if event.get("tool_name") in WHOLE_FILE:
                read = [v for p in paths if (v := self._reads.get((session, design_id, p)))]
                bases[design_id] = min(read, default=bases[design_id])
        self._calls[session, str(event.get("tool_use_id", ""))] = _Call(bases, touched, time.monotonic())

    async def _end(self, session: str, origin: str, event: dict[str, Any], cwd: str) -> None:
        call = self._calls.pop((session, str(event.get("tool_use_id", ""))), None)
        if call is None:  # no start seen (the hook came up meanwhile): from the version last written
            touched = self._touched(event, cwd)
            call = _Call(dict.fromkeys(touched), touched, time.monotonic())
        if event.get("tool_name") in READS:
            for design_id, paths in call.paths.items():
                for path in paths:
                    self._reads[session, design_id, path] = (
                        call.bases[design_id] or self.designs.files(design_id).version
                    )
            return
        for design_id, base in call.bases.items():
            if await self.designs.take_in(design_id, origin, base) is not None:
                self.builds.schedule_build(design_id)

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
        return "Meanwhile, in the hitlcad designs you work in (read a file again before you edit it):\n" + "\n".join(
            lines
        )


def _since(state: DesignState, told: int, origin: str) -> list[str]:
    where = f"{state.name!r} ({state.path})"
    changes = [c for c in state.changes if c.version > told and c.origin != origin]
    lines = [f"- {where}: {_who(c.origin)} changed {', '.join(c.files)}" + (f": {c.summary}" if c.summary else "")
             for c in changes]  # fmt: skip
    for crossed in (x for x in state.crossed if x.version > told and origin in (x.origin, *x.others)):
        others = ", ".join(_who(o) for o in {crossed.origin, *crossed.others} - {origin}) or "another edit"
        lines += [f"- {where}: {c.path} line {c.line} was changed by you and {others} at once, and the merge kept "
                  f"both: it reads `{c.now}` now. Make it what it should be." for c in crossed.crossed]  # fmt: skip
    return lines


def _who(origin: str) -> str:
    if origin == "panel":
        return "the human, in the panel,"
    if origin.startswith("agent:"):
        return "another agent session"
    return "a save to the folder" if origin == "disk" else "hitlcad"
