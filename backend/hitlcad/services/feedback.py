"""What the human points at in the panel: the selection and pins, described against a build (its instances,
their parts' B-rep or meshes, their connectors) and carried onto each new build."""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from hitlcad.analysis.model import BuiltDesign
from hitlcad.core.events import PinsChanged, SelectionChanged
from hitlcad.core.report import Revision
from hitlcad.core.types import Pick, Pin, PinMotion, SelectionItem
from hitlcad.kernel.errors import NOT_FOUND_PIN, NOT_FOUND_REVISION
from hitlcad.review.anchors import AnchorSettings
from hitlcad.services.designs import DesignService


class FeedbackService:
    def __init__(self, designs: DesignService, anchors: AnchorSettings, loaded: int) -> None:
        self.designs = designs
        self.anchors = anchors
        self.loaded = loaded  # built revisions kept in memory: picks and carries reload the same few
        self._loaded: OrderedDict[tuple[str, int], BuiltDesign] = OrderedDict()
        self._lock = threading.Lock()  # built() runs in worker threads

    # ------------------------------------------------------------------ builds of revisions

    def built_revision(self, design_id: str, revision: int | None) -> Revision:
        """`revision`, or the latest successful one of the default configuration; it must have built."""
        state = self.designs.get(design_id)
        rev = state.revision(revision) if revision else state.latest(None)
        if rev is None or rev.status != "succeeded":
            raise NOT_FOUND_REVISION(
                f"revision {revision} did not build" if revision else "there is no successful build yet"
            )
        return self.designs.revision(design_id, rev.id)

    def revision_dir(self, design_id: str, rev: Revision) -> Path:
        return self.designs.store.revision_dir(design_id, rev.id)

    def built(self, design_id: str, rev: Revision) -> BuiltDesign:
        from hitlcad.build.pipeline import load_built

        key = (design_id, rev.id)
        with self._lock:
            built = self._loaded.get(key)
        if built is None:  # loaded outside the lock: other builds stay available meanwhile
            assert rev.spec is not None  # a revision that built
            built = load_built(rev.report, rev.spec, Path(self.designs.get(design_id).path),
                               self.revision_dir(design_id, rev))  # fmt: skip
        with self._lock:
            self._loaded[key] = built
            self._loaded.move_to_end(key)
            while len(self._loaded) > self.loaded:
                self._loaded.popitem(last=False)
        return built

    async def describe_picks(self, design_id: str, revision: int | None, picks: list[Pick]) -> list[SelectionItem]:
        from hitlcad.review.selection import describe_build_pick

        rev = self.built_revision(design_id, revision)
        built = await asyncio.to_thread(self.built, design_id, rev)
        items = []
        for pick in picks:
            item = await asyncio.to_thread(describe_build_pick, built, pick.point, pick.normal, pick.component)
            items.append(item.model_copy(update={"revision": rev.id}))
        return items

    # ------------------------------------------------------------------ selection & pins

    async def set_selection(
        self, design_id: str, picks: list[Pick], revision: int | None = None
    ) -> list[SelectionItem]:
        items = await self.describe_picks(design_id, revision, picks) if picks else []
        self.designs.emit(design_id, SelectionChanged(items=items))
        return items

    def _pins(self, design_id: str, pin_ids: set[str]) -> list[Pin]:
        """The design's pins, after checking that each of ``pin_ids`` is one of them."""
        pins = self.designs.get(design_id).pins
        if missing := pin_ids - {p.id for p in pins}:
            raise NOT_FOUND_PIN(f"pin {', '.join(sorted(missing))} not found")
        return pins

    def _save_pins(self, design_id: str, pins: list[Pin]) -> None:
        self.designs.emit(design_id, PinsChanged(pins=pins))

    async def add_pin(self, design_id: str, pick: Pick, note: str = "", revision: int | None = None,
                      motion: PinMotion | None = None) -> Pin:  # fmt: skip
        """A pin at a pick (as assembled), placed while the view showed a range of motion at `motion`, if it did."""
        (described,) = await self.describe_picks(design_id, revision, [pick])
        pin = Pin(point=pick.point, normal=pick.normal, instance=described.instance, component=described.component,
                  connector=described.connector, note=note, label=described.label, revision=described.revision,
                  motion=motion)  # fmt: skip
        self._save_pins(design_id, [*self.designs.get(design_id).pins, pin])
        return pin

    def update_pin(self, design_id: str, pin_id: str, **changes: Any) -> Pin:
        """Change a pin's ``note``, ``resolved`` or ``reply``."""
        pins = self._pins(design_id, {pin_id})
        updated = {k: v for k, v in changes.items() if v is not None}
        pins = [p.model_copy(update=updated) if p.id == pin_id else p for p in pins]
        self._save_pins(design_id, pins)
        return next(p for p in pins if p.id == pin_id)

    def remove_pin(self, design_id: str, pin_id: str) -> None:
        self._save_pins(design_id, [p for p in self._pins(design_id, {pin_id}) if p.id != pin_id])

    def resolve_pins(self, design_id: str, pin_ids: list[str], reply: str = "") -> list[Pin]:
        wanted = set(pin_ids)
        pins = [p.model_copy(update={"resolved": True, "reply": reply}) if p.id in wanted else p
                for p in self._pins(design_id, wanted)]  # fmt: skip
        self._save_pins(design_id, pins)
        return [p for p in pins if p.id in wanted]

    # ------------------------------------------------------------------ across rebuilds

    async def carry(self, design_id: str, revision: Revision) -> None:
        """Move open pins and the selection of the same configuration onto a new build where they clearly match."""
        from hitlcad.review.anchors import carry

        state = self.designs.get(design_id)
        target = await asyncio.to_thread(self.built, design_id, revision)
        same = {r.id for r in state.revisions if r.configuration == revision.configuration}
        pins = [p for p in state.pins if not p.resolved and p.revision in same and p.revision != revision.id]
        items = [i for i in state.selection if i.revision in same and i.revision != revision.id]
        moved: dict[str, Pick] = {}
        for source in sorted({x.revision for x in [*pins, *items] if x.revision is not None}):
            old_info = state.revision(source)
            if old_info is None or old_info.status != "succeeded":
                continue
            old = self.designs.revision(design_id, source)
            batch = [x for x in [*pins, *items] if x.revision == source]
            picks = [
                (Pick(point=x.point, normal=x.normal, component=x.component), x.connector, x.instance) for x in batch
            ]
            built = await asyncio.to_thread(self.built, design_id, old)
            carried = await asyncio.to_thread(carry, built, target, picks, self.anchors)
            moved |= {x.id: pick for x, pick in zip(batch, carried, strict=True) if pick is not None}
        if not moved:
            return
        described = dict(
            zip(moved, await self.describe_picks(design_id, revision.id, list(moved.values())), strict=True)
        )
        if any(p.id in described for p in pins):
            self._save_pins(design_id, [
                p.model_copy(update={"point": d.point, "instance": d.instance, "component": d.component,
                                     "connector": d.connector, "label": d.label, "revision": revision.id})
                if (d := described.get(p.id)) else p
                for p in self.designs.get(design_id).pins
            ])  # fmt: skip
        if any(i.id in described for i in items):
            selection = [
                described[i.id].model_copy(update={"id": i.id}) if i.id in described else i for i in state.selection
            ]
            self.designs.emit(design_id, SelectionChanged(items=selection))
