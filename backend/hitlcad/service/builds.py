"""Building design folders: on request, after the human edits parameters, and debounced after files change.

A rebuild whose spec (backend, parameters, source, manufacturing, requirements) matches the last
build's is skipped, which also stops write-backs from triggering themselves.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from hitlcad.core.events import BuildStarted, ErrorRaised, RevisionCreated
from hitlcad.core.types import BuildSpec, Revision, RevisionOrigin, apply_values
from hitlcad.designs.folder import DESIGN_FILE, DesignFile, DesignFolder
from hitlcad.pipeline import BuildPipeline
from hitlcad.service.designs import DesignService, NotFoundError
from hitlcad.service.feedback import FeedbackService

log = logging.getLogger(__name__)

DEBOUNCE_MS = 400


class BuildService:
    def __init__(self, designs: DesignService, pipeline: BuildPipeline, feedback: FeedbackService) -> None:
        self.designs = designs
        self.pipeline = pipeline
        self.feedback = feedback
        self._built: dict[str, str] = {}  # design id -> spec key of the last build
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending: set[str] = set()

    def _load(self, design_id: str, folder: DesignFolder) -> tuple[DesignFile, BuildSpec | None]:
        """The folder's design.json and build spec (None until the model source exists), synced into the
        log. Invalid files are reported in the panel and raised."""
        try:
            design = folder.load()
            spec = folder.spec(design) if folder.source_path(design).exists() else None
        except (OSError, ValueError) as e:
            self.designs.emit(design_id, ErrorRaised(message=f"{DESIGN_FILE} or model source is invalid: {e}"))
            raise ValueError(f"cannot build {folder.path}: {e}") from e
        self.designs.sync(design_id, design)
        return design, spec

    async def build(self, design_id: str, summary: str = "", origin: RevisionOrigin = RevisionOrigin.FILE) -> Revision:
        """Build the folder's current files now."""
        folder = self.designs.folder(design_id)
        async with self._locks[design_id]:
            design, spec = self._load(design_id, folder)
            if spec is None:
                raise ValueError(
                    f"cannot build {folder.path}: {design.source} does not exist yet: write the model first"
                )
            return await self._run(design_id, folder, spec, summary or f"Built from {design.source}", origin)

    async def rebuild(self, design_id: str) -> Revision | None:
        """Build after the files changed; None when there is no model yet or the spec is the last build's."""
        folder = self.designs.folder(design_id)
        async with self._locks[design_id]:
            _, spec = self._load(design_id, folder)
            if spec is None or self._built.get(design_id) == spec.key:
                return None
            return await self._run(design_id, folder, spec, "Rebuilt after the files changed", RevisionOrigin.FILE)

    async def _run(
        self, design_id: str, folder: DesignFolder, spec: BuildSpec, summary: str, origin: RevisionOrigin
    ) -> Revision:
        designs = self.designs
        self._built[design_id] = spec.key
        state = designs.get(design_id)
        rev_id = len(state.revisions) + 1
        designs.emit(design_id, BuildStarted(backend=spec.backend, origin=origin.value))
        try:
            report = await self.pipeline.run(spec, designs.store.revision_dir(design_id, rev_id))
        except Exception as e:  # a bug in hitlcad: end the build visibly, then let it surface
            designs.emit(design_id, ErrorRaised(message=f"the build crashed: {e}"))
            raise
        revision = Revision(id=rev_id, origin=origin, spec=spec, report=report, summary=summary,
                            parent=state.current_revision.id if state.current_revision else None)  # fmt: skip
        designs.emit(design_id, RevisionCreated(revision=revision))
        if report.status == "succeeded":
            self._export(design_id, folder, revision)  # after the revision: its event clears the design's error
            await self.feedback.carry(design_id, revision)
        return revision

    def _export(self, design_id: str, folder: DesignFolder, revision: Revision) -> None:
        try:
            folder.export_build(self.designs.store.revision_dir(design_id, revision.id), revision.report)
        except OSError as e:  # the revision built; say in the panel that its files are not in build/
            self.designs.emit(
                design_id,
                ErrorRaised(message=f"revision {revision.id} built, but {folder.build_dir} could not be written: {e}"),
            )

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

        asyncio.get_running_loop().create_task(run())

    def catch_up(self) -> None:
        """Build registered folders whose files differ from their latest build: new designs, and
        folders edited while hitlcad was not running."""
        for path, design_id in self.designs.folders().items():
            if latest := self.designs.get(design_id).current_revision:
                self._built.setdefault(design_id, latest.spec.key)
            self.designs.sync(design_id)
            try:
                changed = DesignFolder(Path(path)).spec().key != self._built.get(design_id)
            except (OSError, ValueError):  # no model yet, or invalid files: the watcher builds once they are saved
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
                                RevisionOrigin.PARAMETER_EDIT)  # fmt: skip
