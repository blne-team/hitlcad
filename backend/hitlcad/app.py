"""Composition root: wires settings, storage, backends, analyses, exporters, rendering, the design services
and the watcher."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hitlcad.analysis.base import Analysis, default_analyses
from hitlcad.backends.registry import BackendRegistry
from hitlcad.build.pipeline import BuildPipeline
from hitlcad.config import Settings, get_settings
from hitlcad.exporters import Exporter, discover
from hitlcad.render.base import Renderer, preview_renderer
from hitlcad.review.anchors import AnchorSettings
from hitlcad.services.builds import BuildService
from hitlcad.services.context import ContextFeed
from hitlcad.services.designs import DesignService
from hitlcad.services.explore import ExploreService
from hitlcad.services.feedback import FeedbackService
from hitlcad.services.watcher import FolderWatcher
from hitlcad.store.sqlite import SqliteStore


def build_pipeline(
    settings: Settings,
    *,
    registry: BackendRegistry | None = None,
    renderer: Renderer | None = None,
    analyses: Sequence[Analysis] | None = None,
    exporters: dict[str, Exporter] | None = None,
) -> BuildPipeline:
    """The build pipeline, whatever is not given from ``settings``: the server's, and ``hitlcad build``'s."""
    return BuildPipeline(registry or BackendRegistry.from_settings(settings), settings,
                         default_analyses() if analyses is None else analyses,
                         discover() if exporters is None else exporters, renderer or preview_renderer(settings))  # fmt: skip


@dataclass
class HitlCad:
    settings: Settings
    registry: BackendRegistry
    pipeline: BuildPipeline
    designs: DesignService
    builds: BuildService
    feedback: FeedbackService
    explore: ExploreService
    context: ContextFeed
    watcher: FolderWatcher
    owns_backends: bool  # made from the settings, not given: it closes them
    stopped: bool = False

    @classmethod
    def create(
        cls,
        settings: Settings | None = None,
        *,
        registry: BackendRegistry | None = None,
        renderer: Renderer | None = None,
        analyses: Sequence[Analysis] | None = None,
        exporters: dict[str, Exporter] | None = None,
    ) -> HitlCad:
        """Defaults come from ``settings``; tests replace the backends (theirs to close: they outlive the server),
        the renderer and the analyses."""
        settings = settings or get_settings()
        pipeline = build_pipeline(settings, registry=registry, renderer=renderer, analyses=analyses,
                                  exporters=exporters)  # fmt: skip
        designs = DesignService(SqliteStore(settings.data_dir), pipeline.registry, settings.loaded_revisions)
        feedback = FeedbackService(designs, AnchorSettings(reach=settings.anchor_reach, tie=settings.anchor_tie,
                                                           aligned_deg=settings.anchor_aligned_deg),
                                   settings.loaded_builds, pipeline.shapes)  # fmt: skip
        builds = BuildService(designs, pipeline, feedback, settings)
        return cls(settings, pipeline.registry, pipeline, designs, builds, feedback, ExploreService(designs, settings),
                   ContextFeed(designs), FolderWatcher(designs, builds), registry is None)  # fmt: skip

    def start(self) -> None:
        for backend in self.registry.all():
            backend.warm()
        self.watcher.start()

    async def shutdown(self) -> None:
        """Once; again, nothing."""
        if self.stopped:
            return
        self.stopped = True
        await self.watcher.stop()
        await self.builds.stop()
        folders = [Path(p) for p in self.designs.folders()]
        for backend in self.registry.all():
            await (backend.close() if self.owns_backends else backend.release(folders))
        self.designs.store.close()
