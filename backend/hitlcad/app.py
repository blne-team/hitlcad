"""Composition root: wires settings, storage, backends, analyses, exporters, rendering, the design services
and the watcher."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hitlcad.analysis.anchors import AnchorSettings
from hitlcad.analysis.base import Analysis, default_analyses
from hitlcad.backends.registry import BackendRegistry
from hitlcad.build.pipeline import BuildPipeline
from hitlcad.config import Settings, get_settings
from hitlcad.designs.watcher import FolderWatcher
from hitlcad.exporters import Exporter, discover
from hitlcad.render.base import BlenderWorkbenchRenderer, NullRenderer, Renderer
from hitlcad.service.builds import BuildService
from hitlcad.service.context import ContextFeed
from hitlcad.service.designs import DesignService
from hitlcad.service.explore import ExploreService
from hitlcad.service.feedback import FeedbackService
from hitlcad.store.fs import FileDesignStore


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
        """Defaults come from ``settings``; tests replace the backends, the renderer and the analyses."""
        settings = settings or get_settings()
        registry = registry or BackendRegistry.from_settings(settings)
        if renderer is None:
            workbench = BlenderWorkbenchRenderer(binary=settings.blender_binary, size=settings.render_size)
            renderer = workbench if workbench.available() else NullRenderer()
        pipeline = BuildPipeline(registry, settings, analyses if analyses is not None else default_analyses(),
                                 exporters if exporters is not None else discover(), renderer)  # fmt: skip
        designs = DesignService(FileDesignStore(settings.designs_dir), registry, settings.folders_file)
        feedback = FeedbackService(designs, AnchorSettings(reach=settings.anchor_reach, tie=settings.anchor_tie,
                                                           aligned_deg=settings.anchor_aligned_deg))  # fmt: skip
        builds = BuildService(designs, pipeline, feedback, settings)
        return cls(settings, registry, pipeline, designs, builds, feedback, ExploreService(designs, settings),
                   ContextFeed(designs), FolderWatcher(designs, builds))  # fmt: skip

    def start(self) -> None:
        self.watcher.start()

    async def shutdown(self) -> None:
        await self.watcher.stop()
        await self.builds.stop()
