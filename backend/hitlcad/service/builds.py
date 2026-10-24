"""Building design folders: on request, after the human edits parameters, and debounced after files change.

A rebuild whose spec (the design, its code, its vendor files' checksums, its components' specs) matches
the last build's is skipped, which also stops write-backs from triggering themselves. A design that
builds also schedules the designs that use it as a component; their specs include its own, so they
rebuild only when it changed.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from hitlcad.backends.base import PlanOutcome
from hitlcad.build.pipeline import BuildPipeline
from hitlcad.config import Settings
from hitlcad.core.design import DesignSpec
from hitlcad.core.events import BuildStarted, ErrorRaised, RevisionCreated
from hitlcad.core.report import BuildSpec, Result, Revision
from hitlcad.core.types import apply_values
from hitlcad.designs.folder import DESIGN_FILE, DesignFolder
from hitlcad.service.designs import DesignService, NotFoundError
from hitlcad.service.feedback import FeedbackService

log = logging.getLogger(__name__)

DEBOUNCE_MS = 400


class BuildService:
    def __init__(self, designs: DesignService, pipeline: BuildPipeline, feedback: FeedbackService,
                 settings: Settings) -> None:  # fmt: skip
        self.designs = designs
        self.pipeline = pipeline
        self.feedback = feedback
        self.settings = settings
        self._built: dict[str, str] = {}  # design id -> spec key of the last build of its default configuration
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def _load(self, design_id: str, folder: DesignFolder, configuration: str | None) -> tuple[DesignSpec, BuildSpec]:
        """design.json and the build spec, synced into the log. Invalid files are reported in the panel and raised."""
        try:
            design = folder.load()
            spec = folder.spec(self.settings.tolerances, configuration)
        except (OSError, ValueError) as e:
            self.designs.emit(design_id, ErrorRaised(message=f"{DESIGN_FILE} or the design's code is invalid: {e}"))
            raise ValueError(f"cannot build {folder.path}: {e}") from e
        self.designs.sync(design_id, design)
        return design, spec

    async def build(self, design_id: str, summary: str = "", origin: str = "file",
                    configuration: str | None = None) -> Revision:  # fmt: skip
        """Build the folder's current files now."""
        folder = self.designs.folder(design_id)
        async with self._locks[design_id]:
            design, spec = self._load(design_id, folder, configuration)
            if not design.parts and not design.components:
                raise ValueError(f"cannot build {folder.path}: design.json declares no parts yet")
            return await self._run(design_id, folder, spec, summary or "Built on request", origin)

    async def build_all(self, design_id: str, summary: str = "") -> list[Revision]:
        """Every configuration: the default one, then each named one."""
        design = self.designs.folder(design_id).load()
        return [await self.build(design_id, summary, configuration=c) for c in [None, *design.configurations]]

    async def check_structure(self, design_id: str) -> tuple[list[Result], PlanOutcome | None]:
        folder = self.designs.folder(design_id)
        _, spec = self._load(design_id, folder, None)
        work = self.settings.cache_dir / "structure" / design_id
        return await self.pipeline.check_structure(spec, folder.path, work)

    async def rebuild(self, design_id: str) -> Revision | None:
        """Build after the files changed; None when there are no parts yet or the spec is the last build's."""
        folder = self.designs.folder(design_id)
        async with self._locks[design_id]:
            design, spec = self._load(design_id, folder, None)
            if not design.parts and not design.components or self._built.get(design_id) == spec.key:
                return None
            return await self._run(design_id, folder, spec, "Rebuilt after the files changed", "file")

    async def _run(self, design_id: str, folder: DesignFolder, spec: BuildSpec, summary: str, origin: str) -> Revision:
        designs = self.designs
        if spec.configuration is None:
            self._built[design_id] = spec.key
        state = designs.get(design_id)
        rev_id = len(state.revisions) + 1
        designs.emit(design_id, BuildStarted(origin=origin, configuration=spec.configuration))
        try:
            report = await self.pipeline.run(spec, folder.path, designs.store.revision_dir(design_id, rev_id))
        except Exception as e:  # a bug in hitlcad: end the build visibly, then let it surface
            self._built.pop(design_id, None)  # so the next save tries again
            designs.emit(design_id, ErrorRaised(message=f"the build crashed: {e}"))
            raise
        previous = state.latest(spec.configuration)
        revision = Revision(id=rev_id, origin=origin, spec=spec, report=report, summary=summary,  # type: ignore[arg-type]
                            parent=previous.id if previous else None)  # fmt: skip
        designs.emit(design_id, RevisionCreated(revision=revision))
        if report.status == "succeeded":
            if spec.configuration is None:
                self._export(design_id, folder, revision)  # after the revision: its event clears the design's error
            await self.feedback.carry(design_id, revision)
            for user in designs.where_used(design_id):
                self.schedule_build(user)
        return revision

    def _export(self, design_id: str, folder: DesignFolder, revision: Revision) -> None:
        try:
            folder.export_build(self.designs.store.revision_dir(design_id, revision.id), revision.report)
        except OSError as e:  # the revision built; say in the panel that its files are not in build/
            self.designs.emit(design_id, ErrorRaised(
                message=f"revision {revision.id} built, but {folder.build_dir} could not be written: {e}"))  # fmt: skip

    def schedule_build(self, design_id: str) -> None:
        """Debounced rebuild, skipped when the inputs did not change."""
        if design_id in self._pending:
            return
        self._pending.add(design_id)

        async def run() -> None:
            await asyncio.sleep(DEBOUNCE_MS / 1000)
            self._pending.discard(design_id)
            try:
                await self.rebuild(design_id)
            except (ValueError, NotFoundError) as e:
                log.info("auto-build of %s skipped: %s", design_id, e)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        """Cancel the builds scheduled in the background."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def catch_up(self) -> None:
        """Build registered folders whose files differ from their latest build: new designs, and folders
        edited while hitlcad was not running."""
        for path, design_id in self.designs.folders().items():
            if latest := self.designs.get(design_id).latest(None):
                self._built.setdefault(design_id, latest.spec.key)
            self.designs.sync(design_id)
            try:
                changed = DesignFolder(Path(path)).spec(self.settings.tolerances).key != self._built.get(design_id)
            except (OSError, ValueError):  # invalid files: the watcher builds once they are saved
                continue
            if changed:
                self.schedule_build(design_id)

    async def edit_parameters(self, design_id: str, values: dict[str, Any]) -> Revision:
        """The human tuned parameters in the panel: write them to design.json and rebuild."""
        folder = self.designs.folder(design_id)
        design = folder.load()
        specs = apply_values(design.parameters, values)
        changed = {p.name: p.value for p, old in zip(specs, design.parameters, strict=True) if p.value != old.value}
        folder.save(design.model_copy(update={"parameters": specs}))
        return await self.build(design_id, "Parameter edit: " + ", ".join(f"{k}={v}" for k, v in changed.items()),
                                "parameter_edit")  # fmt: skip
