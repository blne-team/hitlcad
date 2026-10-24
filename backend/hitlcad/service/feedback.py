"""What the human points at in the panel: the selection and pins, described against a build's
geometry (its STEP when it has one, else its mesh) and carried onto each new build."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hitlcad.core.events import PinsChanged, SelectionChanged
from hitlcad.core.types import Pick, Pin, Revision, SelectionItem
from hitlcad.service.designs import DesignService, NotFoundError


class FeedbackService:
    def __init__(self, designs: DesignService) -> None:
        self.designs = designs

    # ------------------------------------------------------------------ geometry of a revision

    def built_revision(self, design_id: str, revision: int | None) -> Revision:
        """`revision`, or the latest successful one; it must have built."""
        state = self.designs.get(design_id)
        rev = state.revision(revision) if revision else state.latest_successful_revision
        if rev is None or rev.report.status != "succeeded":
            raise NotFoundError(
                f"revision {revision} did not build" if revision else "there is no successful build yet"
            )
        return rev

    def step_file(self, design_id: str, revision: int | None) -> tuple[Revision, Path]:
        """The B-rep (STEP) of a built revision; only build123d designs have one."""
        rev = self.built_revision(design_id, revision)
        step = rev.report.artifact("exchange")
        if step is None:
            raise NotFoundError(f"revision {rev.id} has no STEP: only build123d designs have faces and edges")
        return rev, self.designs.store.revision_dir(design_id, rev.id) / step.path

    def geometry_file(self, design_id: str, rev: Revision) -> Path:
        """What clicks on a built revision are described against: its STEP if it has one, else its named
        objects (Blender), else its mesh."""
        geometry = rev.report.artifact("exchange") or rev.report.artifact("objects") or rev.report.artifact("mesh")
        assert geometry is not None  # every successful build has a mesh
        return self.designs.store.revision_dir(design_id, rev.id) / geometry.path

    async def describe_picks(self, design_id: str, revision: int | None, picks: list[Pick]) -> list[SelectionItem]:
        from hitlcad.analysis.selection import describe_pick

        rev = self.built_revision(design_id, revision)
        target = self.geometry_file(design_id, rev)
        items = []
        for pick in picks:
            item = await asyncio.to_thread(describe_pick, target, pick.point, pick.normal, pick.component)
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
            raise NotFoundError(f"pin {', '.join(sorted(missing))} not found")
        return pins

    def _save_pins(self, design_id: str, pins: list[Pin]) -> None:
        self.designs.emit(design_id, PinsChanged(pins=pins))

    async def add_pin(self, design_id: str, pick: Pick, note: str = "", revision: int | None = None) -> Pin:
        (described,) = await self.describe_picks(design_id, revision, [pick])
        pin = Pin(point=pick.point, normal=pick.normal, component=pick.component, note=note, label=described.label,
                  revision=described.revision)  # fmt: skip
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
        """Move open pins and the selection onto a new build where their geometry clearly matches."""
        from hitlcad.analysis.anchors import carry

        state = self.designs.get(design_id)
        target = self.geometry_file(design_id, revision)
        pins = [p for p in state.pins if not p.resolved and p.revision is not None and p.revision != revision.id]
        items = [i for i in state.selection if i.revision is not None and i.revision != revision.id]
        moved: dict[str, Pick] = {}  # pin / selection id -> its pick on the new build
        for source in sorted({x.revision for x in [*pins, *items] if x.revision is not None}):
            old = state.revision(source)
            if old is None or old.report.status != "succeeded":
                continue
            batch = [x for x in [*pins, *items] if x.revision == source]
            picks = [Pick(point=x.point, normal=x.normal, component=x.component) for x in batch]
            carried = await asyncio.to_thread(carry, self.geometry_file(design_id, old), target, picks)
            moved |= {x.id: pick for x, pick in zip(batch, carried, strict=True) if pick is not None}
        if not moved:
            return
        described = dict(
            zip(moved, await self.describe_picks(design_id, revision.id, list(moved.values())), strict=True)
        )
        if any(p.id in described for p in pins):
            self._save_pins(design_id, [
                p.model_copy(update={"point": d.point, "component": d.component, "label": d.label, "revision": revision.id})
                if (d := described.get(p.id)) else p
                for p in self.designs.get(design_id).pins
            ])  # fmt: skip
        if any(i.id in described for i in items):
            selection = [
                described[i.id].model_copy(update={"id": i.id}) if i.id in described else i for i in state.selection
            ]
            self.designs.emit(design_id, SelectionChanged(items=selection))
