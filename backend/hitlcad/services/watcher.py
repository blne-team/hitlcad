"""Rebuild design folders when their files change on disk (saved by a person or an agent)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from hitlcad.services.builds import DEBOUNCE_MS, BuildService
from hitlcad.services.designs import DesignService
from hitlcad.workspace.folder import BUILD_DIR

log = logging.getLogger(__name__)


class FolderWatcher:
    def __init__(self, designs: DesignService, builds: BuildService) -> None:
        self.designs = designs
        self.builds = builds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._watch())
            self._task.add_done_callback(_ended)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _watch(self) -> None:
        from watchfiles import awatch

        changed = self.designs.folders_changed
        while True:
            registered = self.designs.folders()
            folders = [Path(f) for f in registered]
            changed.clear()
            await self.builds.catch_up()  # new folders, and edits made while nothing watched
            if not folders:
                await changed.wait()
                continue
            stop = asyncio.Event()
            restart = asyncio.ensure_future(changed.wait())  # a folder was added: watch the new set
            restart.add_done_callback(lambda _, stop=stop: stop.set())
            try:
                async for changes in awatch(*folders, stop_event=stop, debounce=DEBOUNCE_MS, recursive=True,
                                            watch_filter=lambda _, path, f=folders: _relevant(f, Path(path))):  # fmt: skip
                    for design_id in {_owner(registered, Path(path)) for _, path in changes}:
                        if design_id:  # skipped when the build's inputs did not change
                            self.builds.schedule_build(design_id)
            except FileNotFoundError:
                await asyncio.sleep(1)  # a folder vanished; folders() drops it next round
            finally:
                restart.cancel()


def _ended(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and (error := task.exception()):
        log.error("the folder watcher stopped: saved files are not rebuilt until hitlcad restarts", exc_info=error)


IGNORED = {BUILD_DIR, f".{BUILD_DIR}.tmp", f".{BUILD_DIR}.old", "__pycache__", ".git"}


def _relevant(folders: list[Path], path: Path) -> bool:
    """Changes that can change a build: not hitlcad's own outputs, caches or editors' temporary files. Only
    the part of `path` inside its design folder counts: the folder itself may be anywhere, under build/ too."""
    inside = [path.relative_to(f) for f in folders if path.is_relative_to(f)]
    return any(not IGNORED & set(rel.parts) for rel in inside) and not path.name.endswith((".tmp", "~", ".swp"))


def _owner(folders: dict[str, str], path: Path) -> str | None:
    """The design whose folder holds `path` (the innermost, when design folders nest)."""
    owners = [(len(f), i) for f, i in folders.items() if path == Path(f) or Path(f) in path.parents]
    return max(owners)[1] if owners else None
