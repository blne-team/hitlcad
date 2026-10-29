"""Take into each design's document what is written to its folder (by a person, an agent, git: anyone but hitlcad,
whose own writes match the document and change nothing), then rebuild it; and so for a design first opened since
the server started, whose folder may have changed while nothing watched it. While an agent's tool that can write a
design runs, its writes are that tool's to take in, from the version it started on (`AgentSessions`)."""

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
            self._tasks = [loop.create_task(self._watch()), loop.create_task(self._catch_up())]
            for task in self._tasks:
                task.add_done_callback(_ended)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _catch_up(self) -> None:
        """Each design as it is first opened: its folder taken in, and built unless its files are its latest
        build's."""
        while True:
            design_id = await self.designs.first_used.get()
            await self._take_in(design_id)
            self.builds.schedule_build(design_id)

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
