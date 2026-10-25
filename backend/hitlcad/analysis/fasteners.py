"""Fastener sets: each stack of holes (through patterns, then the into hole) shares one axis, a fastener sits
seated on each stack, its shank clears every through hole by ISO 273, its thread is the into hole's, and it engages
the into hole by at least the into part's material minimum, with the space past it along its axis free."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hitlcad.analysis.base import Context, label, result, structural
from hitlcad.analysis.mates import allowance
from hitlcad.analysis.model import BuiltDesign, Leaf, PlacedFrame, StructureError
from hitlcad.core import frames as fr
from hitlcad.core.report import Cause, Result
from hitlcad.core.structure import FastenerSet

#: What a fastener's catalog provider must give.
FASTENER_DATA = ("thread", "thread_diameter", "thread_pitch", "length", "clearance_fine", "clearance_medium")
#: A made into hole takes the thread when it is narrower than the thread's major diameter and at least this share of
#: it: a tap drill, or the smaller hole a thread-forming screw cuts its own thread in (in plastics, down to 0.7 d).
TAPPED = 0.7
#: Rays parallel to the fastener's axis, round it, that look for what stops it past the into hole's opening.
FREE_RAYS = 8
#: Those rays run this share of the way out to the thread's root (or the into hole's wall, if narrower): inside
#: the fastener, clear of the hole's own wall, which they would otherwise graze.
FREE_REACH = 0.9
#: ISO 68-1's basic profile: a thread's minor diameter is its major diameter less this many pitches (5/4 of the
#: fundamental triangle's height √3/2 · P).
MINOR_PITCHES = 5 * math.sqrt(3) / 8


def _coaxial(a: PlacedFrame, b: PlacedFrame, tol: Any) -> bool:
    za, zb = fr.z_axis(a.frame), fr.z_axis(b.frame)
    parallel = min(fr.angle_deg(za, zb), fr.angle_deg(za, -zb)) <= tol.angle_deg
    return parallel and fr.distance_to_line(fr.origin(b.frame), fr.origin(a.frame), za) <= tol.position_mm


def stacks(built: BuiltDesign, fs: FastenerSet) -> list[tuple[PlacedFrame, list[PlacedFrame]]]:
    """Each stack (the through frame of every through pattern on an into frame's axis, then the into frame)
    with its fastener seat: the free seat nearest the stack's top."""
    seats = built.frames(fs.fastener)
    throughs = [built.frames(t) for t in fs.through]
    into = built.frames(fs.into)
    out = []
    for anchor in into:
        stack = []
        for end, pattern in zip(fs.through, throughs, strict=True):
            hits = [f for f in pattern if _coaxial(anchor, f, built.tolerances)]
            if len(hits) != 1:
                raise StructureError(f"{label(anchor)} has {len(hits)} holes of {end} on its axis; a stack needs one")
            stack.append(hits[0])
        out.append([*stack, anchor])
    if len(out) != len(seats):
        raise StructureError(
            f"{len(out)} hole stacks and {len(seats)} placed {fs.fastener.split('.')[0]}: one fastener per stack"
        )
    free, paired = list(seats), []
    for stack in out:
        seat = min(free, key=lambda s: float(np.linalg.norm(fr.origin(s.frame) - fr.origin(stack[0].frame))))
        free.remove(seat)
        paired.append((seat, stack))
    return paired


def bore(hole: PlacedFrame, tol: Any) -> float:
    """The narrowest of the coaxial holes that run on, end to end, from a through hole's frame: what a shank passes
    (the hole under a counterbore, not the counterbore)."""
    features, local = hole.part.features, hole.part.frames[hole.connector][hole.index]
    narrowest, depth = hole.measured.values["diameter"], hole.measured.values["offset"] + hole.measured.values["length"]
    while (deeper := features.round(local @ fr.translation((0, 0, -depth)), tol, inward=True)).status == "pass":
        narrowest = min(narrowest, deeper.values["diameter"])
        depth += deeper.values["offset"] + deeper.values["length"]
    return narrowest


def free_depth(
    leaves: list[Leaf], seat: PlacedFrame, into: PlacedFrame, radius: float, reach: float
) -> tuple[float, str]:
    """How far the fastener's path is free past the into hole's opening, within `reach`, in the assembled design: the
    first surface a ray along its axis or round it at `radius` enters, of any instance but the fastener's own, and
    that instance; (inf, "") when nothing is in its way."""
    axis = fr.z_axis(seat.frame)
    start = fr.origin(into.frame)
    side = fr.x_axis(seat.frame)
    angles = np.linspace(0, 2 * np.pi, FREE_RAYS, endpoint=False)
    ring = np.outer(np.cos(angles), side) + np.outer(np.sin(angles), np.cross(axis, side))
    origins = np.vstack([start, start + radius * ring])
    path = np.vstack([origins, origins + axis * reach])
    span = np.array([path.min(axis=0), path.max(axis=0)])
    nearest, what = math.inf, ""
    for leaf in leaves:
        if leaf.path == seat.instance or np.any(leaf.bounds[0] > span[1]) or np.any(leaf.bounds[1] < span[0]):
            continue
        inverse = np.linalg.inv(leaf.transform)
        local = origins @ inverse[:3, :3].T + inverse[:3, 3]
        direction = inverse[:3, :3] @ axis
        distance = float(leaf.part.features.probe.entering(local, np.tile(direction, (len(local), 1))).min())
        if distance < nearest:
            nearest, what = distance, leaf.path
    return (nearest, what) if nearest <= reach else (math.inf, "")


class Fasteners:
    name = "fastener"
    title = "Fastener sets"
    summary = "Each fastener seats, clears its holes, takes the thread and engages enough"

    def run(self, ctx: Context) -> list[Result]:
        out, leaves = [], ctx.built.leaves()
        for fs in ctx.built.spec.fasteners:
            try:
                paired = stacks(ctx.built, fs)
            except StructureError as e:
                out.append(structural(fs.address, e, [fs.fastener, *fs.through, fs.into]))
                continue
            out += [self._stack(ctx, leaves, fs, seat, stack) for seat, stack in paired]
        return out

    def _stack(self, ctx: Context, leaves: list[Leaf], fs: FastenerSet, seat: PlacedFrame,
               stack: list[PlacedFrame]) -> Result:  # fmt: skip
        *through, into = stack
        about = [seat.instance, *(f.instance for f in stack), seat.address, *(f.address for f in stack)]
        problems, notes, values = [], [], {}
        gap = float(np.linalg.norm(fr.origin(seat.frame) - fr.origin(through[0].frame)))
        tilt = fr.angle_deg(fr.z_axis(seat.frame), -fr.z_axis(through[0].frame))
        if gap > ctx.tol.position_mm or tilt > ctx.tol.angle_deg:
            problems.append(f"not seated on {label(through[0])} ({gap:.3f} mm, {tilt:.2f}° off)")
        meta = seat.part.catalog.metadata if seat.part.catalog else {}
        if missing := [k for k in FASTENER_DATA if k not in meta]:
            return result(self.name, fs.address, None, f"{label(seat)}: its catalog provider gives no "
                          f"{', '.join(missing)}, so clearance, thread and engagement cannot be checked", about)  # fmt: skip
        thread, d = str(meta["thread"]), float(meta["thread_diameter"])
        pitch, length = float(meta["thread_pitch"]), float(meta["length"])
        for hole in through:
            if hole.measured.status != "pass":
                notes.append(f"{label(hole)}: clearance not measured ({hole.measured.detail})")
                continue
            made = bore(hole, ctx.tol) - allowance(hole, "hole")
            fine, medium = float(meta["clearance_fine"]), float(meta["clearance_medium"])
            if made < fine - ctx.tol.position_mm - hole.measured.uncertainty_mm:
                problems.append(f"the {thread} shank does not clear {label(hole)}: Ø{made:.3f}"
                                + (" as made" if allowance(hole, "hole") else "")
                                + f", ISO 273 asks Ø{fine:g} (fine) or Ø{medium:g} (medium)")  # fmt: skip
        if mismatch := self._thread(into, thread, d, pitch, ctx.tol):
            problems.append(mismatch)
        past = length - fr.along(fr.origin(into.frame), fr.origin(seat.frame), fr.z_axis(seat.frame))
        depth = into.measured.values["length"] if into.measured.status == "pass" else math.inf
        engagement = min(past, depth)
        values["engagement_mm"] = engagement
        k = fs.engagement
        material = into.part.material
        if k is None and material in into.owner.materials:
            k = into.owner.materials[material].min_thread_engagement_d
        if k is None:
            notes.append(f"engagement {engagement:.2f} mm not judged: {into.part.name} has no material in the registry")
        elif engagement < k * d:
            problems.append(f"engagement {engagement:.2f} mm in {label(into)}, needs {k * d:.2f} ({k:g} × Ø{d:g})")
        radius = FREE_REACH * min(into.measured.values.get("diameter", d), d - MINOR_PITCHES * pitch) / 2
        free, stop = free_depth(leaves, seat, into, radius, past + ctx.tol.position_mm)
        cause = ""
        if past > free + ctx.tol.position_mm:
            values["free_mm"] = free
            cause = Cause.overlap(seat.instance, stop)  # it reaches into what stops it: when that is all, its cause
            problems.append(f"bottoms out: it reaches {past:.2f} mm past the opening of {label(into)}, where {stop} "
                            f"stops it at {free:.2f}")  # fmt: skip
        status: bool | None = False if problems else (None if notes else True)
        head = f"{label(seat)} through {', '.join(label(h) for h in through)} into {label(into)}"
        detail = "; ".join(problems + notes) or f"seated, clears, engages {engagement:.2f} mm"
        return result(self.name, fs.address, status, f"{head}: {detail}", about, values,
                      cause=cause if len(problems) == 1 else "")  # fmt: skip

    @staticmethod
    def _thread(into: PlacedFrame, thread: str, d: float, pitch: float, tol: Any) -> str | None:
        """Why the fastener's thread is not the into hole's: a catalog part's thread by its data, a made hole by its
        size."""
        meta = into.part.catalog.metadata if into.part.catalog else {}
        if "thread_diameter" in meta:
            theirs = (float(meta["thread_diameter"]), float(meta.get("thread_pitch", pitch)))
            if not np.allclose(theirs, (d, pitch)):
                size = meta.get("thread", f"Ø{theirs[0]:g}")
                return f"its {thread} thread does not fit the {size} thread of {label(into)}"
            return None
        if into.measured.status != "pass":
            return None  # the into connector's own result says why
        made = into.measured.values["diameter"] - allowance(into, "hole")
        slack = tol.position_mm + into.measured.uncertainty_mm
        if not TAPPED * d - slack <= made < d:
            return (
                f"{label(into)} (Ø{made:.3f}) takes no {thread} thread: tapped or thread-forming, its hole lies "
                f"from Ø{TAPPED * d:.2f} to under Ø{d:g}"
            )
        return None
