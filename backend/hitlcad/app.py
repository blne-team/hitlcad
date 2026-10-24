"""Composition root: wires settings, storage, backends, rendering, the design services and the watcher."""

from __future__ import annotations

from dataclasses import dataclass

from hitlcad.backends.registry import BackendRegistry
from hitlcad.config import Settings, get_settings
from hitlcad.designs.watcher import FolderWatcher
from hitlcad.pipeline import BuildPipeline
from hitlcad.render.base import BlenderWorkbenchRenderer, NullRenderer, Renderer
from hitlcad.service.builds import BuildService
from hitlcad.service.context import ContextFeed
from hitlcad.service.designs import DesignService
from hitlcad.service.feedback import FeedbackService
from hitlcad.store.fs import FileDesignStore


@dataclass
class HitlCad:
    settings: Settings
    registry: BackendRegistry
    designs: DesignService
    builds: BuildService
    feedback: FeedbackService
    context: ContextFeed
    watcher: FolderWatcher

    @classmethod
    def create(
        cls,
        settings: Settings | None = None,
        *,
        registry: BackendRegistry | None = None,
        renderer: Renderer | None = None,
    ) -> HitlCad:
        """Defaults come from ``settings``; tests replace the backends and the renderer."""
        settings = settings or get_settings()
        registry = registry or BackendRegistry.from_settings(settings)
        if renderer is None:
            workbench = BlenderWorkbenchRenderer(binary=settings.blender_binary, size=settings.render_size)
            renderer = workbench if workbench.available() else NullRenderer()
        designs = DesignService(FileDesignStore(settings.designs_dir), registry, settings.folders_file)
        feedback = FeedbackService(designs)
        builds = BuildService(designs, BuildPipeline(registry, renderer), feedback)
        return cls(settings, registry, designs, builds, feedback, ContextFeed(designs), FolderWatcher(designs, builds))

    def start(self) -> None:
        self.watcher.start()

    async def shutdown(self) -> None:
        await self.watcher.stop()
