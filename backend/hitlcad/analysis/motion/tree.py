"""The exact sweep: a mate's moving side, and the gears it drives, each about or along a fixed axis.

A range is swept from where the mate sits towards each of its ends (both ways round, for a full turn)
by conservative advancement: at each position the gap between every moving instance and what it can
reach is measured, and the next position is no further on than lets any point travel that gap (the gap
over its largest distance from the axis, for a turn), so nothing passes through anything between two
positions. Where instances touch, a step moves no point further than `verification.position_mm`, so a
contact is placed to that much. What is round about the axis of a turn, of either instance (a hub on
its shaft, a knuckle in its hinge, a duct's wall), turns within itself and keeps its gap, so only the
rest of each is moved against the other; and an instance that meets nothing anywhere in a full turn (a
blade inside its duct) needs no positions at all: what it passes through in a full turn is the solid of
revolution of its profile. Instances that overlap where they sit beyond what a mate allows (an ISO
clearance fit modelled at its basic size touches, to its facets) are the interference analysis's to
report: motion leaves them out and says so. The least gap of each pair a clearance requirement names is
kept for the requirement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from pydantic import Field

from hitlcad.analysis.base import ResultCache
from hitlcad.analysis.interference import boxes_touch
from hitlcad.analysis.model import SEARCH_MM, WIDEN, AnalysisSettings, Leaf, gap
from hitlcad.analysis.motion.findings import Contact, Findings, Least, Pair, Stop, at_rest
from hitlcad.analysis.motion.plans import Drive, Plan, drives
from hitlcad.analysis.motion.turning import axis_frame, not_round, profile, radius, revolve

if TYPE_CHECKING:
    import manifold3d


class Swept(Findings):
    """What a plan's sweep found."""

    positions: int = 0
    stopped: list[Stop] = Field(default_factory=list)


def sweep(plan: Plan, settings: AnalysisSettings, cache: ResultCache) -> Swept:
    """The plan swept through each of its ranges: once per plan, and cached by what it depends on."""
    if plan.swept is None:
        plan.swept = _Sweep(plan, settings, cache).run()
    return plan.swept


@dataclass
class _Pair(Pair):
    """A pair, the drives that move each (`dq` None: q stays), and once shaped, the solids the sweep moves (placed
    where they sit) and how fast the gap between them can close (mm per unit of the mate's motion)."""

    dp: Drive
    dq: Drive | None
    a: manifold3d.Manifold | None = None
    b: manifold3d.Manifold | None = None
    speed: float = 0.0

    def solids(self, value: float) -> tuple[manifold3d.Manifold, manifold3d.Manifold]:
        assert self.a is not None and self.b is not None
        b = self.b if self.dq is None else self.b.transform(self.dq.at(value)[:3, :])
        return self.a.transform(self.dp.at(value)[:3, :]), b

    def key(self) -> list[object]:
        """What its sweep depends on, once `at_rest` has kept it: its leaves where they sit, how they move,
        the gap they must keep, and what they may share where they sit (what a mate allows)."""
        return [self.p.path, self.p.part.digest, np.round(self.p.transform, 6).tolist(), self.dp.key(), self.q.path,
                self.q.part.digest, np.round(self.q.transform, 6).tolist(), self.dq.key() if self.dq else None,
                self.need, self.tracked, round(self.sitting_mm3, 6)]  # fmt: skip


@dataclass
class _Track:
    """A pair as one direction sweeps it: where its gap is next measured, how far that measurement looks, and
    the gap it must keep from here on."""

    pair: _Pair
    need: float
    search: float
    next: float = 0.0
    gap: float = math.inf


class _Sweep:
    def __init__(self, plan: Plan, settings: AnalysisSettings, cache: ResultCache) -> None:
        self.plan, self.settings, self.cache = plan, settings, cache
        self.tol = plan.world.tolerances
        self.round_mm = settings.mesh_tolerance_mm + self.tol.position_mm  # a facetted round is round to this
        self.least: dict[tuple[str, str], Least] = {}
        self.shapes: dict[tuple[object, ...], tuple[manifold3d.Manifold, float]] = {}
        self.turned: dict[tuple[object, ...], manifold3d.Manifold] = {}

    def run(self) -> Swept:
        """The sweep, cached by the pairs it moves. Which pairs it moves, and what each may share where it sits,
        follow from the mates' allowances (`overlap`, `fit` and the measured diameters); they are settled first,
        outside the cache, so an edit to a mate's allowance sweeps again."""
        prepared, settled = [], Swept()
        for kind, lo, hi, continuous in self.plan.ranges:
            driven = drives(self.plan, kind)[0]  # not stuck: `plans` gives those to a loop solver
            rest = self.plan.free.get(kind, 0.0)
            ends = (
                [rest + 180.0, rest - 180.0]
                if continuous
                else [*([hi] if hi > rest else []), *([lo] if lo < rest else [])]
            )
            pairs = at_rest(self._pairs(driven, [end - rest for end in ends]), self.plan.world, self.settings, self.cache,
                            settled)  # fmt: skip
            prepared.append((kind, rest, ends, pairs))
        key = self.cache.key("motion", [[kind, rest, ends, [p.key() for p in pairs]] for kind, rest, ends, pairs in prepared],
                             self.settings.overlap_mm3, self.settings.mesh_tolerance_mm, self.tol.position_mm,
                             self.tol.motion_steps)  # fmt: skip
        swept = Swept.model_validate(self.cache.get_or(key, lambda: self._swept(prepared).model_dump()))
        return swept.model_copy(update={
            "resting": sorted(set(settled.resting)), "unmeasurable": sorted(set(settled.unmeasurable)),
            "least": [least.model_copy(update={"complete": least.complete and not settled.unmeasurable})
                      for least in swept.least],
        })  # fmt: skip

    def _swept(self, prepared: list[tuple[str, float, list[float], list[_Pair]]]) -> Swept:
        out = Swept()
        for kind, rest, ends, kept in prepared:
            pairs = [p for p in kept if self._shaped(p) and not self._clear_in_a_turn(p, kind)]
            for end in ends:
                if not out.stopped:
                    self._advance(kind, rest, end, pairs, out)
        out.least = [least.model_copy(update={"complete": not out.stopped}) for least in self.least.values()]
        return out

    def _pairs(self, drives: dict[str, Drive], deltas: list[float]) -> list[_Pair]:
        """Each moving leaf with every other leaf its motion may bring it near, but those that move with it."""
        w = self.plan.world
        moving = {p: drives[c] for p in w.leaves if (c := w.clusters.find(p)) in drives}
        boxes = {p: _swept_bounds(w.leaves[p], d, [0.0, *deltas]) for p, d in moving.items()}
        out = []
        for p, dp in moving.items():
            for q, other in w.leaves.items():
                dq = moving.get(q)
                if q == p or dq is dp or (dq is not None and q < p):
                    continue
                need = w.tracked.get(frozenset((p, q)))
                if boxes_touch(
                    boxes[p], other.bounds if dq is None else boxes[q], (need or 0.0) + self.tol.position_mm
                ):
                    out.append(_Pair(w.leaves[p], other, need or 0.0, need is not None, dp, dq))
        return out

    def _shaped(self, pair: _Pair) -> bool:
        """Give the pair the solids the sweep moves; False when nothing of them can meet. A leaf turning past
        one that stays put moves only its part that is not round about the axis against the other's part
        that is not (`not_round`): what is round of either turns within itself, and its gap to the other
        stays what it is where they sit. Two moving leaves are moved whole."""
        if pair.dq is not None:
            pair.a, pair.b = pair.p.manifold, pair.q.manifold
            pair.speed = sum(d.speed(radius(s, axis_frame(d.axis))) for d, s in ((pair.dp, pair.a), (pair.dq, pair.b)))
            return True
        (pair.a, reach), (pair.b, _) = self._shape(pair.p, pair.dp), self._shape(pair.q, pair.dp)
        pair.speed = pair.dp.speed(reach)
        if pair.a.is_empty() or pair.b.is_empty():
            return False
        pair.sitting_mm3 = float((pair.a ^ pair.b).volume()) if pair.sitting_mm3 else 0.0
        return True

    def _shape(self, leaf: Leaf, drive: Drive) -> tuple[manifold3d.Manifold, float]:
        """The leaf less what is round about the drive's axis (all of it, along a travel), and how far that
        reaches from the axis."""
        key = (leaf.path, *drive.key())
        if key not in self.shapes:
            frame = axis_frame(drive.axis)
            solid = not_round(leaf.manifold, frame, self.round_mm) if drive.axis.kind == "angle" else leaf.manifold
            self.shapes[key] = (solid, radius(solid, frame))
        return self.shapes[key]

    def _clear_in_a_turn(self, pair: _Pair, kind: str) -> bool:
        """Whether nothing the moving solid passes through in a full turn meets the other (which stays put), so
        that no position of any range about the axis can; a pair a clearance requirement names must also
        keep its gap through the turn, and the least gap through it is recorded."""
        if pair.dq is not None or pair.dp.axis.kind != "angle":
            return False
        assert pair.a is not None and pair.b is not None
        key = (pair.p.path, *pair.dp.key())
        if key not in self.turned:
            frame = axis_frame(pair.dp.axis)
            self.turned[key] = revolve(profile(pair.a, frame, 0.0), frame, self.tol.position_mm)
        turned = self.turned[key]
        if (turned ^ pair.b).volume() > pair.sitting_mm3 + self.settings.overlap_mm3:
            return False
        if not pair.tracked:
            return True
        span = float(np.linalg.norm(np.ptp(np.vstack([pair.p.bounds, pair.q.bounds]), axis=0)))
        sitting = gap(pair.a, pair.b, span)
        least = min(sitting, float(turned.min_gap(pair.b, sitting + self.tol.position_mm)))
        if least < pair.need:
            return False
        self._record(pair, least, kind, None)
        return True

    def _advance(self, kind: str, rest: float, end: float, pairs: list[_Pair], out: Swept) -> None:
        """From where the mate sits towards `end` by conservative advancement (see the module's docstring), up
        to the first contact, or until the positions run out."""
        sign, span = math.copysign(1.0, end - rest), abs(end - rest)
        step_mm, budget = self.tol.position_mm, self.tol.motion_steps
        tracks = [_Track(p, p.need, p.need + SEARCH_MM) for p in pairs if p.speed > 0]
        x = clear = 0.0
        while True:
            hit: tuple[_Pair, float] | None = None
            for t in (t for t in tracks if t.next <= x):
                a, b = t.pair.solids(sign * x)
                found = float(a.min_gap(b, t.search))
                if found >= t.search:  # nothing within the search: at least that far apart
                    reach = t.need + t.pair.speed * (span - x) + step_mm
                    if t.search >= reach:
                        t.next = math.inf  # it cannot close the gap before the end
                        continue
                    lower, t.search = t.search, min(t.search * WIDEN, reach)
                else:
                    lower = t.gap = found
                    if t.pair.tracked:
                        self._record(t.pair, found, kind, rest + sign * x)
                    t.need = t.need if found >= t.need else 0.0  # its clearance is broken here: now only contact
                    if (
                        found < step_mm
                        and (shared := float((a ^ b).volume())) > t.pair.sitting_mm3 + self.settings.overlap_mm3
                    ):
                        hit = (t.pair, shared) if hit is None or shared > hit[1] else hit
                    t.search = max(2 * found, t.need + SEARCH_MM)
                t.next = x + max(lower - t.need, step_mm) / t.pair.speed
            if hit:
                out.contacts.append(Contact(kind=kind, at=rest + sign * x, end=end, clear=rest + sign * clear,
                                            a=hit[0].p.path, b=hit[0].q.path, overlap_mm3=round(hit[1], 6)))  # fmt: skip
                return
            clear = x
            following = min((t.next for t in tracks), default=math.inf)
            if x >= span or following == math.inf:
                return
            if out.positions >= budget:
                closest = min(tracks, key=lambda t: t.next)
                out.stopped.append(Stop(kind=kind, at=rest + sign * x, a=closest.pair.p.path, b=closest.pair.q.path,
                                        gap_mm=round(closest.gap, 6)))  # fmt: skip
                return
            x = min(following, span)
            out.positions += 1

    def _record(self, pair: _Pair, found: float, kind: str, at: float | None) -> None:
        key = (pair.p.path, pair.q.path)
        if key not in self.least or found < self.least[key].gap_mm:
            self.least[key] = Least(a=pair.p.path, b=pair.q.path, gap_mm=round(found, 6), kind=kind,
                                    at=None if at is None else round(at, 6), complete=True)  # fmt: skip


def _swept_bounds(leaf: Leaf, drive: Drive, deltas: list[float]) -> np.ndarray:
    """A box around everything the leaf passes through: moved by each of `deltas` along a travel, anywhere in
    a full turn about an axis."""
    z = drive.axis.direction / np.linalg.norm(drive.axis.direction)
    if drive.axis.kind == "travel":
        shifts = np.outer([drive.rate * d for d in deltas], z)
        return np.array([leaf.bounds[0] + shifts.min(axis=0), leaf.bounds[1] + shifts.max(axis=0)])
    d = leaf.vertices - drive.axis.origin
    along = d @ z
    far = float(np.linalg.norm(d - np.outer(along, z), axis=1).max())
    ends = np.array([drive.axis.origin + along.min() * z, drive.axis.origin + along.max() * z])
    spread = far * np.sqrt(np.clip(1 - z * z, 0, 1))
    return np.array([ends.min(axis=0) - spread, ends.max(axis=0) + spread])
