"""Fastener sets: each stack of holes (through patterns, then the into hole) shares one axis, a fastener sits
seated on each stack, its shank clears every through hole, and its thread engages the into hole by at
least the into part's material minimum without bottoming out."""

from __future__ import annotations

from typing import Any

import numpy as np

from hitlcad.analysis.base import Context, label, result, structural
from hitlcad.analysis.model import PlacedFrame, StructureError
from hitlcad.core import frames as fr
from hitlcad.core.report import Result
from hitlcad.core.structure import FastenerSet


def _coaxial(a: PlacedFrame, b: PlacedFrame, tol: Any) -> bool:
    za, zb = fr.z_axis(a.frame), fr.z_axis(b.frame)
    parallel = min(fr.angle_deg(za, zb), fr.angle_deg(za, -zb)) <= tol.angle_deg
    return parallel and fr.distance_to_line(fr.origin(b.frame), fr.origin(a.frame), za) <= tol.position_mm


def stacks(ctx: Context, fs: FastenerSet) -> tuple[list[PlacedFrame], list[list[PlacedFrame]]]:
    """The fastener seats, and each into frame with the through frame of every through pattern on its axis."""
    seats = ctx.built.frames(fs.fastener)
    throughs = [ctx.built.frames(t) for t in fs.through]
    into = ctx.built.frames(fs.into)
    out = []
    for anchor in into:
        stack = []
        for end, pattern in zip(fs.through, throughs, strict=True):
            hits = [f for f in pattern if _coaxial(anchor, f, ctx.tol)]
            if len(hits) != 1:
                raise StructureError(f"{label(anchor)} has {len(hits)} holes of {end} on its axis; a stack needs one")
            stack.append(hits[0])
        out.append([*stack, anchor])
    if len(out) != len(seats):
        raise StructureError(
            f"{len(out)} hole stacks and {len(seats)} placed {fs.fastener.split('.')[0]}: one fastener per stack"
        )
    return seats, out


class Fasteners:
    name = "fastener"

    def run(self, ctx: Context) -> list[Result]:
        out = []
        for fs in ctx.built.spec.fasteners:
            try:
                seats, all_stacks = stacks(ctx, fs)
            except StructureError as e:
                out.append(structural(fs.address, e, [fs.fastener, *fs.through, fs.into]))
                continue
            free = list(seats)
            for stack in all_stacks:
                top = stack[0]
                seat = min(
                    free, key=lambda s: float(np.linalg.norm(fr.origin(s.frame) - fr.origin(top.frame))), default=None
                )
                if seat is None:
                    break
                free.remove(seat)
                out.append(self._stack(ctx, fs, seat, stack))
        return out

    def _stack(self, ctx: Context, fs: FastenerSet, seat: PlacedFrame, stack: list[PlacedFrame]) -> Result:
        *through, into = stack
        about = [seat.instance, *(f.instance for f in stack), seat.address, *(f.address for f in stack)]
        problems, notes, values = [], [], {}
        gap = float(np.linalg.norm(fr.origin(seat.frame) - fr.origin(through[0].frame)))
        tilt = fr.angle_deg(fr.z_axis(seat.frame), -fr.z_axis(through[0].frame))
        if gap > ctx.tol.position_mm or tilt > ctx.tol.angle_deg:
            problems.append(f"not seated on {label(through[0])} ({gap:.3f} mm, {tilt:.2f}° off)")
        meta = seat.part.catalog.metadata if seat.part.catalog else {}
        if "thread_diameter" not in meta or "length" not in meta:
            return result(self.name, fs.address, None, f"{label(seat)}: its catalog provider gives no thread_diameter "
                          "and length, so clearance and engagement cannot be checked", about)  # fmt: skip
        d, length = float(meta["thread_diameter"]), float(meta["length"])
        for hole in through:
            if hole.measured.status != "pass" or "diameter" not in hole.measured.values:
                notes.append(f"{label(hole)}: clearance not measured ({hole.measured.detail})")
            elif hole.measured.values["diameter"] <= d:
                problems.append(
                    f"the Ø{d:g} shank does not clear {label(hole)} (Ø{hole.measured.values['diameter']:.2f})"
                )
        engagement = length - fr.along(fr.origin(into.frame), fr.origin(seat.frame), fr.z_axis(seat.frame))
        values["engagement_mm"] = engagement
        k = fs.engagement
        material = into.part.material
        if k is None and material in into.owner.materials:
            k = into.owner.materials[material].min_thread_engagement_d
        if k is None:
            notes.append(f"engagement {engagement:.2f} mm not judged: {into.part.name} has no material in the registry")
        elif engagement < k * d:
            problems.append(f"engagement {engagement:.2f} mm in {label(into)}, needs {k * d:.2f} ({k:g} × Ø{d:g})")
        depth = into.measured.values.get("length") if into.measured.status == "pass" else None
        if depth is not None and engagement > depth + ctx.tol.position_mm:
            problems.append(f"bottoms out: {engagement:.2f} mm into a {depth:.2f} mm deep {label(into)}")
        status: bool | None = False if problems else (None if notes else True)
        head = f"{label(seat)} through {', '.join(label(h) for h in through)} into {label(into)}"
        detail = "; ".join(problems + notes) or f"seated, clears, engages {engagement:.2f} mm"
        return result(self.name, fs.address, status, f"{head}: {detail}", about, values)
