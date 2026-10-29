"""Take into each design's document what is written to its folder (by a person, an agent, git: anyone but hitlcad,
whose own writes match the document and change nothing), then rebuild it; and, as the server starts, what was written
while nothing watched, the design rebuilt once it is first opened. While an agent's tool that can write a design
runs, its writes are that tool's to take in, as the agent's (`AgentSessions`)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from hitlcad.services.builds import DEBOUNCE_MS, BuildService
from hitlcad.services.designs import DesignService
from hitlcad.services.sessions import AgentSessions
from hitlcad.workspace.document import in_design

log = logging.getLogger(__name__)


class FolderWatcher:
    def __init__(self, designs: DesignService, builds: BuildService, sessions: AgentSessions) -> None:
        self.designs = designs
        self.builds = builds
        self.sessions = sessions
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        if not self._tasks:
            loop = asyncio.get_running_loop()
            self._tasks = [loop.create_task(self._watch()), loop.create_task(self._build_when_used())]
            for task in self._tasks:
                task.add_done_callback(_ended)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def catch_up(self) -> None:
        """Each registered design's folder, taken in: what was written while no server watched it, so nothing reads
        a design behind its folder. Building waits until the design is opened (`_build_when_used`): a server knows
        every design it ever opened."""
        for design_id in self.designs.folders().values():
            await self._take_in(design_id)

    async def _build_when_used(self) -> None:
        """Each design as it is first opened since the server started: built unless its files are its latest
        build's."""
        while True:
            self.builds.schedule_build(await self.designs.first_used.get())

    async def _take_in(self, design_id: str) -> bool:
        try:
            return await self.designs.take_in(design_id) is not None
        except OSError as e:  # a file went while it was read: the next change reads it again
            log.info("could not read %s: %s", self.designs.get(design_id).path, e)
            return False

    async def _watch(self) -> None:
        from watchfiles import awatch

        changed = self.designs.folders_changed
        while True:
            folders = [Path(f) for f in self.designs.folders()]
            changed.clear()
            if not folders:
                await changed.wait()
                continue
            stop = asyncio.Event()
            restart = asyncio.ensure_future(changed.wait())  # a folder was added: watch the new set
            restart.add_done_callback(lambda _, stop=stop: stop.set())
            try:
                async for changes in awatch(*folders, stop_event=stop, debounce=DEBOUNCE_MS, recursive=True,
                                            watch_filter=lambda _, path, f=folders: _relevant(f, Path(path))):  # fmt: skip
                    for design_id in {self.designs.owner(Path(path)) for _, path in changes}:
                        if design_id and not self.sessions.busy(design_id) and await self._take_in(design_id):
                            self.builds.schedule_build(design_id)
            except FileNotFoundError:
                await asyncio.sleep(1)  # a folder vanished; folders() drops it next round
            finally:
                restart.cancel()


def _ended(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and (error := task.exception()):
        log.error("the folder watcher stopped: saved files are not rebuilt until hitlcad restarts", exc_info=error)


def _relevant(folders: list[Path], path: Path) -> bool:
    """Changes to a design's files: not hitlcad's own outputs, caches or editors' temporary files. Only the part of
    `path` inside its design folder counts: the folder itself may be anywhere, under build/ too."""
    return any(in_design(path.relative_to(f)) for f in folders if path.is_relative_to(f))
