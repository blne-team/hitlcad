"""Building design folders: on request, after the human edits parameters, and debounced after files change.

A rebuild whose spec (the design, its code, its vendor files' checksums, its components' specs) matches
the last build's is skipped, which also stops write-backs from triggering themselves. A design that
builds also schedules the designs that use it as a component; their specs include its own, so they
rebuild only when it changed. A design.json that does not validate makes a failed revision as any build that
cannot produce geometry does, with a result for each problem; what stops a build that is not the design's to
fix is raised, and shown in the panel as the design's error.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from hitlcad.analysis.base import reported
from hitlcad.backends.base import PlanOutcome
from hitlcad.build.pipeline import BuildPipeline
from hitlcad.config import Settings
from hitlcad.core.events import BuildStarted, ErrorRaised, MotionsWatched
from hitlcad.core.report import BuildReport, BuildSpec, BuildStatus, Result, Revision, results_key
from hitlcad.core.types import WatchedMotion, apply_values
from hitlcad.kernel.errors import (
    DESIGN_UNREADABLE,
    EXPORT_FAILED,
    PARAMETER_INVALID,
    REQUEST_INVALID,
    DesignInvalid,
    HitlcadError,
    diagnose,
)
from hitlcad.services.designs import DesignService, readable
from hitlcad.services.feedback import FeedbackService
from hitlcad.workspace.folder import DesignFolder

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

    async def _load(self, design_id: str, folder: DesignFolder, configuration: str | None) -> BuildSpec | list[Result]:
        """The build spec (reading every source and checksumming vendor files, off the event loop), its
        design.json synced into the log; or, when the files do not make one, the results that say why."""
        try:
            spec = await asyncio.to_thread(folder.spec, self.settings.tolerances, configuration)
        except DesignInvalid as e:
            return [reported("build", p) for p in e.problems]
        except (OSError, HitlcadError) as e:
            d = (
                DESIGN_UNREADABLE.diagnostic(f"cannot read {folder.path}: {e}")
                if isinstance(e, OSError)
                else e.diagnostic
            )
            if d.owner == "design":  # a component's
                return [reported("build", d)]
            self.designs.emit(design_id, ErrorRaised(diagnostic=d))  # a plugin missing, files gone: the panel says so
            raise HitlcadError(d) from e
        self.designs.sync(design_id, spec.design)
        return spec

    async def build(self, design_id: str, summary: str = "", origin: str = "file",
                    configuration: str | None = None) -> Revision:  # fmt: skip
        """Build the folder's current files now."""
        folder = self.designs.folder(design_id)
        summary = summary or "Built on request"
        async with self._locks[design_id]:
            spec = await self._load(design_id, folder, configuration)
            if isinstance(spec, list):
                return self._invalid(design_id, spec, summary, origin, configuration)
            if not spec.design.parts and not spec.design.components:
                raise REQUEST_INVALID(f"cannot build {folder.path}: design.json declares no parts yet")
            if configuration is not None and configuration not in spec.design.configurations:
                raise REQUEST_INVALID(f"no configuration {configuration!r}; configurations: "
                                      + (", ".join(spec.design.configurations) or "none"))  # fmt: skip
            return await self._run(design_id, folder, spec, summary, origin)

    async def build_all(self, design_id: str, summary: str = "") -> list[Revision]:
        """Every configuration: the default one, then each named one (just the default one while design.json
        does not validate)."""
        design = readable(self.designs.folder(design_id))
        configurations = [*design.configurations] if design else []
        return [await self.build(design_id, summary, configuration=c) for c in [None, *configurations]]

    async def check_structure(self, design_id: str) -> tuple[list[Result], PlanOutcome | None]:
        folder = self.designs.folder(design_id)
        spec = await self._load(design_id, folder, None)
        if isinstance(spec, list):
            return spec, None
        work = self.settings.cache_dir / "structure" / design_id
        return await self.pipeline.check_structure(spec, folder.path, work)

    async def rebuild(self, design_id: str) -> Revision | None:
        """Build after the files changed; None when there are no parts yet or the files are the last build's."""
        folder = self.designs.folder(design_id)
        async with self._locks[design_id]:
            spec = await self._load(design_id, folder, None)
            if isinstance(spec, list):
                key = results_key(spec)
                if self._built.get(design_id) == key:
                    return None
                self._built[design_id] = key
                return self._invalid(design_id, spec, "Rebuilt after the files changed", "file", None)
            if not spec.design.parts and not spec.design.components or self._built.get(design_id) == spec.key:
                return None
            return await self._run(design_id, folder, spec, "Rebuilt after the files changed", "file")

    def _invalid(self, design_id: str, results: list[Result], summary: str, origin: str,
                 configuration: str | None) -> Revision:  # fmt: skip
        """A failed revision of files that make no build spec: design.json does not validate."""
        state = self.designs.get(design_id)
        previous = state.latest(configuration)
        report = BuildReport(status=BuildStatus.FAILED, configuration=configuration, duration_s=0.0, results=results)
        revision = Revision(id=len(state.revisions) + 1, origin=origin, configuration=configuration, spec=None,  # type: ignore[arg-type]
                            report=report, summary=summary, parent=previous.id if previous else None)  # fmt: skip
        self.designs.add_revision(design_id, revision)
        return revision

    async def _run(self, design_id: str, folder: DesignFolder, spec: BuildSpec, summary: str, origin: str) -> Revision:
        designs = self.designs
        if spec.configuration is None:
            self._built[design_id] = spec.key
        state = designs.get(design_id)
        rev_id = len(state.revisions) + 1
        designs.emit(design_id, BuildStarted(origin=origin, configuration=spec.configuration))
        try:
            watched = {w.key: w.revision for w in state.watched}
            report = await self.pipeline.run(spec, folder.path, designs.store.revision_dir(design_id, rev_id), watched)
        except Exception as e:  # the machine stopped it, or a bug: end the build visibly, then let it surface
            self._built.pop(design_id, None)  # so the next save tries again
            d = diagnose(e)
            if d.owner == "hitlcad":
                log.exception("the build of %s crashed", folder.path)
            designs.emit(design_id, ErrorRaised(diagnostic=d))
            raise
        previous = state.latest(spec.configuration)
        revision = Revision(id=rev_id, origin=origin, configuration=spec.configuration, spec=spec, report=report,  # type: ignore[arg-type]
                            summary=summary, parent=previous.id if previous else None)  # fmt: skip
        designs.add_revision(design_id, revision)
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
            self.designs.emit(design_id, ErrorRaised(diagnostic=EXPORT_FAILED.diagnostic(
                f"revision {revision.id} built, but {folder.build_dir} could not be written: {e}")))  # fmt: skip

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
            except HitlcadError as e:  # the panel shows it, as the design's error, where it is not the call's
                log.info("auto-build of %s stopped: %s", design_id, e)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        """Cancel the builds scheduled in the background."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def catch_up(self) -> None:
        """Build registered folders whose files differ from their latest build: new designs, and folders
        edited while hitlcad was not running."""
        for path, design_id in self.designs.folders().items():
            if latest := self.designs.get(design_id).latest(None):
                self._built.setdefault(design_id, latest.key)  # type: ignore[arg-type]
            self.designs.sync(design_id)
            key = await asyncio.to_thread(self._spec_key, Path(path))
            if key is None or key != self._built.get(design_id):
                self.schedule_build(design_id)

    def _spec_key(self, path: Path) -> str | None:
        """What a build of the folder's files depends on, as a key; None when they are invalid (the build
        reports them in the panel)."""
        try:
            return DesignFolder(path).spec(self.settings.tolerances).key
        except (OSError, HitlcadError):
            return None

    async def watch_motion(self, design_id: str, revision: int, key: str, watched: bool = True) -> Revision:
        """The human watched a loop's motion move in the panel (`MotionRange.key` of `revision`), or takes that back:
        record it, and rebuild that configuration so that its results say so."""
        rev = self.designs.revision(design_id, revision)
        motion = next((m for m in rev.report.motions if m.key and m.key == key), None)
        if motion is None:
            raise REQUEST_INVALID(f"revision {revision} has no loop motion {key!r} to watch")
        others = [w for w in self.designs.get(design_id).watched if w.key != key]
        mine = [WatchedMotion(key=key, pair=motion.pair, revision=revision)] if watched else []
        self.designs.emit(design_id, MotionsWatched(watched=others + mine))
        return await self.build(design_id, f"{'Watched' if watched else 'Unwatched'} {motion.pair}", "review",
                                rev.configuration)  # fmt: skip

    async def edit_parameters(self, design_id: str, values: dict[str, Any]) -> Revision:
        """The human tuned parameters in the panel: write them to design.json and rebuild."""
        folder = self.designs.folder(design_id)
        design = folder.load()
        try:
            specs = apply_values(design.parameters, values)
        except ValueError as e:  # an unknown parameter, or a value out of its range
            raise PARAMETER_INVALID(str(e)) from None
        changed = {p.name: p.value for p, old in zip(specs, design.parameters, strict=True) if p.value != old.value}
        folder.save(design.model_copy(update={"parameters": specs}))
        return await self.build(design_id, "Parameter edit: " + ", ".join(f"{k}={v}" for k, v in changed.items()),
                                "parameter_edit")  # fmt: skip
