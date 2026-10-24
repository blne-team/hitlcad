"""Rebuild design folders when their files change on disk (saved by a person or an agent)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from hitlcad.designs.folder import BUILD_DIR
from hitlcad.service.builds import DEBOUNCE_MS, BuildService
from hitlcad.service.designs import DesignService


class FolderWatcher:
    def __init__(self, designs: DesignService, builds: BuildService) -> None:
        self.designs = designs
        self.builds = builds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._watch())

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
            folders = list(registered)
            changed.clear()
            self.builds.catch_up()  # new folders, and edits made while nothing watched
            if not folders:
                await changed.wait()
                continue
            stop = asyncio.Event()
            restart = asyncio.ensure_future(changed.wait())  # a folder was added: watch the new set
            restart.add_done_callback(lambda _, stop=stop: stop.set())
            try:
                async for changes in awatch(*folders, stop_event=stop, debounce=DEBOUNCE_MS, recursive=True,
                                            watch_filter=lambda _, path: _relevant(Path(path))):  # fmt: skip
                    for design_id in {_owner(registered, Path(path)) for _, path in changes}:
                        if design_id:  # skipped when the build's inputs did not change
                            self.builds.schedule_build(design_id)
            except FileNotFoundError:
                await asyncio.sleep(1)  # a folder vanished; folders() drops it next round
            finally:
                restart.cancel()


IGNORED = {BUILD_DIR, f".{BUILD_DIR}.tmp", f".{BUILD_DIR}.old", "__pycache__", ".git"}


def _relevant(path: Path) -> bool:
    """Changes that can change a build: not hitlcad's own outputs, caches or editors' temporary files."""
    return not (IGNORED & set(path.parts)) and not path.name.endswith((".tmp", "~", ".swp"))


def _owner(folders: dict[str, str], path: Path) -> str | None:
    """The design whose folder holds `path` (the innermost, when design folders nest)."""
    owners = [(len(f), i) for f, i in folders.items() if path == Path(f) or Path(f) in path.parents]
    return max(owners)[1] if owners else None
