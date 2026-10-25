"""Fastener sets: each stack of holes (through patterns, then the into hole) shares one axis, a fastener sits
seated on each stack, its shank clears every through hole by ISO 273, its thread is the into hole's, and it engages
the into hole by at least the into part's material minimum, with the space past it along its axis free."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import trimesh

from hitlcad.analysis.base import Context, label, result, structural
from hitlcad.analysis.mates import allowance
from hitlcad.analysis.model import Leaf, PlacedFrame, StructureError
from hitlcad.core import frames as fr
from hitlcad.core.report import Result
from hitlcad.core.structure import FastenerSet

#: What a fastener's catalog provider must give.
FASTENER_DATA = ("thread", "thread_diameter", "thread_pitch", "length", "clearance_fine", "clearance_medium")
#: A made into hole takes the thread when it is narrower than the thread's major diameter and at least this share of
#: it: a tap drill, or the smaller hole a thread-forming screw cuts its own thread in (in plastics, down to 0.7 d).
TAPPED = 0.7
#: Rays parallel to the fastener's axis, round it, that look for what stops it past the into hole's opening.
FREE_RAYS = 8


def _coaxial(a: PlacedFrame, b: PlacedFrame, tol: Any) -> bool:
    za, zb = fr.z_axis(a.frame), fr.z_axis(b.frame)
    parallel = min(fr.angle_deg(za, zb), fr.angle_deg(za, -zb)) <= tol.angle_deg
    return parallel and fr.distance_to_line(fr.origin(b.frame), fr.origin(a.frame), za) <= tol.position_mm


def stacks(ctx: Context, fs: FastenerSet) -> list[tuple[PlacedFrame, list[PlacedFrame]]]:
    """Each stack (the through frame of every through pattern on an into frame's axis, then the into frame)
    with its fastener seat: the free seat nearest the stack's top."""
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
        corners = trimesh.bounds.corners(leaf.part.mesh.bounds) @ leaf.transform[:3, :3].T + leaf.transform[:3, 3]
        if leaf.path == seat.instance or np.any(corners.min(axis=0) > span[1]) or np.any(corners.max(axis=0) < span[0]):
            continue
        inverse = np.linalg.inv(leaf.transform)
        local = origins @ inverse[:3, :3].T + inverse[:3, 3]
        direction = inverse[:3, :3] @ axis
        mesh = leaf.part.mesh
        with np.errstate(divide="ignore", invalid="ignore"):  # trimesh's test of sliver triangles, never hit
            at, ray, tri = mesh.ray.intersects_location(local, np.tile(direction, (len(local), 1)), multiple_hits=True)
        if not len(ray):
            continue
        entering = mesh.face_normals[tri] @ direction < 0
        distance = np.einsum("ij,j->i", at - local[ray], direction)[entering]
        distance = distance[distance > 1e-6]
        if len(distance) and float(distance.min()) < nearest:
            nearest, what = float(distance.min()), leaf.path
    return (nearest, what) if nearest <= reach else (math.inf, "")


class Fasteners:
    name = "fastener"

    def run(self, ctx: Context) -> list[Result]:
        out, leaves = [], ctx.built.leaves()
        for fs in ctx.built.spec.fasteners:
            try:
                paired = stacks(ctx, fs)
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
        radius = 0.9 * min(into.measured.values.get("diameter", d), d - 1.0825 * pitch) / 2  # inside its thread
        free, stop = free_depth(leaves, seat, into, radius, past + ctx.tol.position_mm)
        if past > free + ctx.tol.position_mm:
            values["free_mm"] = free
            problems.append(f"bottoms out: it reaches {past:.2f} mm past the opening of {label(into)}, where {stop} "
                            f"stops it at {free:.2f}")  # fmt: skip
        status: bool | None = False if problems else (None if notes else True)
        head = f"{label(seat)} through {', '.join(label(h) for h in through)} into {label(into)}"
        detail = "; ".join(problems + notes) or f"seated, clears, engages {engagement:.2f} mm"
        return result(self.name, fs.address, status, f"{head}: {detail}", about, values)

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
