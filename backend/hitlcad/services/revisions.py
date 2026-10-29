"""The builds' files: each successful build's parts, scene and outputs, which only the builds something needs keep
(the latest of each configuration, those the human points at); another's are made again when it is opened, from
what it built (`BuiltFrom`), and a build is loaded back from them as the analyses saw it."""

from __future__ import annotations

import asyncio
import shutil
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from hitlcad.analysis.model import BuiltDesign
from hitlcad.core.report import Revision
from hitlcad.services.designs import DesignService

if TYPE_CHECKING:
    from hitlcad.build.pipeline import BuildPipeline


class RevisionFiles:
    def __init__(self, designs: DesignService, pipeline: BuildPipeline, loaded: int) -> None:
        self.designs = designs
        self.pipeline = pipeline  # makes a revision's files again; its part shapes, those a build loaded back shares
        self.loaded = loaded  # built revisions kept in memory: picks and carries reload the same few
        self._loaded: OrderedDict[tuple[str, int], BuiltDesign] = OrderedDict()
        self._making: defaultdict[Path, asyncio.Lock] = defaultdict(asyncio.Lock)  # a revision's files, made again

    async def files(self, design_id: str, rev: Revision) -> Path:
        """Where a revision's files are: made again from what it built (`BuiltFrom`) when it no longer keeps them.
        One that did not build keeps its log."""
        directory = self.designs.store.revision_dir(design_id, rev.id)
        if rev.spec is None or rev.report.status != "succeeded":
            return directory
        async with self._making[directory]:
            if not directory.is_dir():
                staging = directory.with_name(f".{directory.name}.tmp")
                shutil.rmtree(staging, ignore_errors=True)
                await self.pipeline.restore(rev.spec, Path(self.designs.get(design_id).path), staging, rev.report)
                staging.rename(directory)  # whole, or not there: a reader never sees half of them
        return directory

    def prune(self, design_id: str) -> None:
        """Let go of the files of the builds nothing needs. Kept: the latest successful build of each configuration,
        and those the open pins, the selection and the watched motions are on; any other is made again when it is
        opened (`files`)."""
        state = self.designs.get(design_id)
        keep = {latest.id for c in {r.configuration for r in state.revisions} if (latest := state.latest(c))}
        keep |= {x.revision for x in [*state.open_pins, *state.selection, *state.watched]}
        for rev in state.revisions:
            if rev.status != "succeeded" or rev.id in keep:
                continue
            directory = self.designs.store.revision_dir(design_id, rev.id)
            making = self._making.get(directory)
            if directory.is_dir() and not (making and making.locked()):  # not while it is made again
                self._loaded.pop((design_id, rev.id), None)
                shutil.rmtree(directory)

    async def built(self, design_id: str, rev: Revision) -> BuiltDesign:
        from hitlcad.build.pipeline import load_built

        key = (design_id, rev.id)
        built = self._loaded.get(key)
        if built is None:
            assert rev.spec is not None  # a revision that built
            directory = await self.files(design_id, rev)
            built = await asyncio.to_thread(load_built, rev.report, rev.spec, Path(self.designs.get(design_id).path),
                                            directory, self.pipeline.shapes)  # fmt: skip
        self._loaded[key] = built
        self._loaded.move_to_end(key)
        while len(self._loaded) > self.loaded:
            self._loaded.popitem(last=False)
        return built
