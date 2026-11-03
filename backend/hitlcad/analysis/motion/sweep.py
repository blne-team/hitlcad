"""An input's sweep: every pair its motion may bring together, measured from where the input sits towards each end
of its range (both ways round, for a full turn), up to the first contact each way.

Each pair is swept on its own, and cached by what it reads: its two parts where they sit, the motions of the two
clusters they move with, what keeps them apart or together. A change elsewhere in the design leaves it as it was. The
plan's sweep puts its pairs' together: each way ends at the first thing any pair finds along it (a contact, a parting,
or a pair given up on: one that stays within the position tolerance of the other for `verification.motion_steps`
positions), with all that is found there.

Each pair's way advances conservatively: its gap is measured, and measured again no sooner than its parts could have
travelled that gap (less what a clearance requirement keeps), so nothing passes through anything unseen. An exact
plan's way is continuous, each pair's travel bounded by its drives' steepest slopes, so a collision between two
positions is never missed: where parts touch, a step moves no point further than `verification.position_mm`, which
is how closely a contact is placed. A traced plan's way only has its positions, each checked, and the human watches
what lies between them. What is round about the axis one part turns on against the other (a hub on its shaft, a
knuckle in its hinge, a duct's wall) turns within itself and keeps its gap, so only the rest of each is moved; and
an exact turn whose solid of revolution meets nothing needs no positions at all. A pair a relation keeps in contact
(a cam and its follower) is measured every `SAMPLE_MM` of travel: standing apart is parting, sharing volume a
collision. Pairs overlapping where they sit beyond what a mate allows are interference's to report: the sweep leaves
them out and says so. The least gap of each pair a clearance requirement names is kept for the requirement.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from pydantic import Field

from hitlcad.analysis.interference import boxes_touch
from hitlcad.analysis.model import AnalysisSettings, Leaf
from hitlcad.analysis.motion.findings import Contact, Findings, Least, Pair, Parted, Stop, at_rest
from hitlcad.analysis.motion.path import Traced, positions
from hitlcad.analysis.motion.plans import Drive, Plan, corners
from hitlcad.analysis.motion.turning import axis_frame, radius
from hitlcad.core import frames as fr
from hitlcad.core.loops import SAMPLE_MM
from hitlcad.core.structure import Axis
from hitlcad.geometry.mesh import Collider
from hitlcad.kernel.cache import Cache
from hitlcad.kernel.model import Model

if TYPE_CHECKING:
    import manifold3d


class Swept(Findings):
    """What a plan's sweep found, and for a traced plan its positions and what a watch of it is of (`key`)."""

    positions: int = 0
    stopped: list[Stop] = Field(default_factory=list)
    traced: Traced | None = None
    key: str = ""


class _Walked(Model):
    """One pair's walk one way: where along the way (`order`) it ended on finding something, and what (one of
    `contact`, `parted`, `stop`), or None where it ran out of way or out of reach; the positions it was measured at
    after the first, and on a pair a clearance requirement names, the gap at each: (order, input, gap)."""

    order: float | None = None
    contact: Contact | None = None
    parted: Parted | None = None
    stop: Stop | None = None
    positions: list[float] = Field(default_factory=list)
    gaps: list[tuple[float, float, float]] = Field(default_factory=list)


class _PairSwept(Model):
    """What one pair's sweep found: each of the plan's ways (none where nothing of them can meet), or the least gap
    of a tracked pair cleared in a full turn."""

    a: str
    b: str
    ways: list[_Walked] = Field(default_factory=list)
    turned: Least | None = None


def sweep(plan: Plan, settings: AnalysisSettings, cache: Cache) -> Swept:
    """The plan swept through its range: once per plan, each pair's sweep cached by what it depends on."""
    if plan.swept is None:
        plan.swept = _sweep(plan, settings, cache)
    return plan.swept


@dataclass
class _Pair(Pair):
    """A pair, the clusters each moves with (`cq` None: q stays), the axis p only turns about against q if it does,
    and once shaped, the solids moved (placed where they sit), their colliders, and on an exact way how fast the gap
    between them can close (mm per unit of the input)."""

    cp: str
    cq: str | None
    turn: Axis | None = None
    a: manifold3d.Manifold | None = None
    b: manifold3d.Manifold | None = None
    ca: Collider | None = None
    cb: Collider | None = None
    speed: float = 0.0

    def key(self) -> list[object]:
        """What its sweep depends on, once `at_rest` has kept it, but the motion."""
        turn = self.turn and [self.turn.kind, np.round(self.turn.origin, 6).tolist(),
                              np.round(self.turn.direction, 6).tolist()]  # fmt: skip
        return [self.p.path, self.p.part.digest, np.round(self.p.transform, 6).tolist(), self.cp, self.q.path,
                self.q.part.digest, np.round(self.q.transform, 6).tolist(), self.cq, self.need, self.tracked,
                round(self.sitting_mm3, 6), self.contact_key(), turn]  # fmt: skip


def _sweep(plan: Plan, settings: AnalysisSettings, cache: Cache) -> Swept:
    traced = None if plan.exact else positions(plan, settings, cache)
    if traced is not None and (traced.why or traced.held or len(traced.values) < 2):
        return Swept(traced=traced)
    motion: _Motion = _Exact(plan) if traced is None else _Traced(plan, traced)
    settled = Swept()
    pairs = at_rest(motion.pairs(), plan.world, settings, cache, settled)
    tol = plan.world.tolerances
    keys = [cache.key("pair sweep", motion.key(p), p.key(), settings.overlap_mm3, settings.mesh_tolerance_mm,
                      tol.position_mm, tol.motion_steps) for p in pairs]  # fmt: skip
    found = [_PairSwept.model_validate(cache.get_or(k, lambda p=p: _pair_sweep(motion, p, settings).model_dump()))
             for k, p in zip(keys, pairs, strict=True)]  # fmt: skip
    swept = _combine(plan, found)
    return swept.model_copy(update={
        "resting": sorted(set(settled.resting)), "unmeasurable": sorted(set(settled.unmeasurable)), "traced": traced,
        "key": cache.key("watch", traced.key, sorted(keys)) if traced is not None else "",
        "least": [least.model_copy(update={"complete": least.complete and not settled.unmeasurable})
                  for least in swept.least],
    })  # fmt: skip


def _pair_sweep(motion: _Motion, pair: _Pair, settings: AnalysisSettings) -> _PairSwept:
    """One pair through each of the plan's ways, each to its first contact or parting, or as far as it can come."""
    out = _PairSwept(a=pair.p.path, b=pair.q.path)
    if not motion.shape(pair, settings):
        return out
    if (cleared := motion.clear_in_a_turn(pair, settings, out)) is not None:
        return cleared
    return out.model_copy(update={"ways": [_walk(motion.way(end, pair), pair, settings, motion.plan)
                                           for end in motion.plan.ends()]})  # fmt: skip


def _combine(plan: Plan, found: list[_PairSwept]) -> Swept:
    """A plan's sweep from its pairs': each way ends at the first thing any pair found along it, and holds what every
    pair found there; the least gaps are those up to where each way ended; a way given up leaves the rest unswept."""
    out = Swept()
    least = {(f.a, f.b): f.turned for f in found if f.turned is not None}
    for k in range(len(plan.ends())):
        walks = [(f, f.ways[k]) for f in found if f.ways]
        ends = [w.order for _, w in walks if w.order is not None]
        cutoff = min(ends, default=math.inf)
        at = [w for _, w in walks if w.order == cutoff]
        hits, parted = [w.contact for w in at if w.contact], [w.parted for w in at if w.parted]
        out.contacts, out.parted = out.contacts + hits, out.parted + parted
        if not (hits or parted) and (stops := [w.stop for w in at if w.stop]):
            out.stopped.append(min(stops, key=lambda s: s.gap_mm))
        out.positions += len({o for _, w in walks for o in w.positions if o <= cutoff})
        for f, w in walks:
            for order, value, gap in w.gaps:
                if order <= cutoff and ((f.a, f.b) not in least or gap < least[f.a, f.b].gap_mm):
                    least[f.a, f.b] = Least(a=f.a, b=f.b, gap_mm=round(gap, 6), kind=plan.kind, at=round(value, 6),
                                            complete=plan.exact)  # fmt: skip
        if out.stopped:
            break
    out.least = [v.model_copy(update={"complete": v.complete and not out.stopped}) for v in least.values()]
    return out


# --------------------------------------------------------------------------- the motion, as a sweep sees it


class _Motion:
    """How a plan's clusters move, for a sweep."""

    def __init__(self, plan: Plan) -> None:
        self.plan, self.world = plan, plan.world

    def pairs(self) -> list[_Pair]:
        """Each moving leaf with every other leaf its motion may bring it near, but those that move with it."""
        w, tol = self.world, self.world.tolerances
        hosts = {p: c for c in self.plan.moving for p in w.leaves_of({c})}
        boxes = {p: self.bounds(w.leaves[p], c) for p, c in hosts.items()}
        out = []
        for p, cp in hosts.items():
            for q, other in w.leaves.items():
                cq = hosts.get(q)
                if q == p or cq == cp or (cq is not None and q < p):
                    continue
                need = w.tracked.get(frozenset((p, q)))
                if boxes_touch(boxes[p], other.bounds if cq is None else boxes[q], (need or 0.0) + tol.position_mm):
                    pair = _Pair(w.leaves[p], other, need or 0.0, need is not None, cp, cq)
                    pair.turn = self.turn(pair)
                    out.append(pair)
        return out

    def bounds(self, leaf: Leaf, cluster: str) -> np.ndarray: ...

    def key(self, pair: _Pair) -> list[object]:
        """What of the motion the pair's sweep depends on: its two clusters' only."""

    def turn(self, pair: _Pair) -> Axis | None:
        """The axis p only turns about against q, if it does."""

    def shape(self, pair: _Pair, settings: AnalysisSettings) -> bool:
        """Give the pair the solids the sweep moves; False when nothing of them can meet. Of a pair turning against
        each other about an axis, only what is not round about it of each is moved (`not_round`)."""
        round_mm = settings.mesh_tolerance_mm + self.world.tolerances.position_mm  # a facetted round is round to this
        if pair.turn is None:
            (pair.a, pair.ca), (pair.b, pair.cb) = (
                (pair.p.manifold, pair.p.collider),
                (pair.q.manifold, pair.q.collider),
            )
            return True
        (pair.a, pair.ca), (pair.b, pair.cb) = (
            pair.p.not_round(pair.turn, round_mm),
            pair.q.not_round(pair.turn, round_mm),
        )
        if pair.ca is None or pair.cb is None:  # all of one is round about the turn: it keeps its gap
            return False
        pair.sitting_mm3 = float((pair.a ^ pair.b).volume()) if pair.sitting_mm3 else 0.0
        return True

    def clear_in_a_turn(self, pair: _Pair, settings: AnalysisSettings, out: _PairSwept) -> _PairSwept | None:
        """The pair's sweep, where it is cleared at once in a full turn; None where it is to be walked."""
        return None

    def way(self, end: float, pair: _Pair) -> _Way: ...


class _Exact(_Motion):
    """An exact plan: every moving cluster about or along a fixed axis by a law of the input."""

    def __init__(self, plan: Plan) -> None:
        super().__init__(plan)
        self.drives: dict[str, Drive] = {c: s.drive for s in plan.stages for c in s.clusters if s.drive}
        deltas = [end - plan.rest for end in plan.ends()]
        self.span = (min(0.0, *deltas), max(0.0, *deltas))

    def bounds(self, leaf: Leaf, cluster: str) -> np.ndarray:
        """A box around everything the leaf passes through: as far as its drive's law takes it along a travel,
        anywhere in a full turn about an axis."""
        drive = self.drives[cluster]
        z = drive.axis.direction / np.linalg.norm(drive.axis.direction)
        if drive.axis.kind == "travel":
            shifts = np.outer(drive.law.bounds(*self.span), z)
            return np.array([leaf.bounds[0] + shifts.min(axis=0), leaf.bounds[1] + shifts.max(axis=0)])
        d = leaf.vertices - drive.axis.origin
        along = d @ z
        far = float(np.linalg.norm(d - np.outer(along, z), axis=1).max())
        ends = np.array([drive.axis.origin + along.min() * z, drive.axis.origin + along.max() * z])
        spread = far * np.sqrt(np.clip(1 - z * z, 0, 1))
        return np.array([ends.min(axis=0) - spread, ends.max(axis=0) + spread])

    def key(self, pair: _Pair) -> list[object]:
        dq = self.drives.get(pair.cq or "")
        return ["exact", self.plan.kind, self.plan.rest, self.plan.ends(), self.drives[pair.cp].key(),
                dq and dq.key()]  # fmt: skip

    def turn(self, pair: _Pair) -> Axis | None:
        drive = self.drives[pair.cp]
        return drive.axis if pair.cq is None and drive.axis.kind == "angle" else None

    def shape(self, pair: _Pair, settings: AnalysisSettings) -> bool:
        if not super().shape(pair, settings):
            return False
        assert pair.a is not None and pair.b is not None
        dp, dq = self.drives[pair.cp], self.drives.get(pair.cq or "")
        pair.speed = sum(
            d.speed(radius(s, axis_frame(d.axis)), *self.span) for d, s in ((dp, pair.a), (dq, pair.b)) if d
        )
        return True

    def clear_in_a_turn(self, pair: _Pair, settings: AnalysisSettings, out: _PairSwept) -> _PairSwept | None:
        """Cleared where nothing the moving solid passes through in a full turn meets the other (which stays put),
        so that no position of any range about the axis can; a pair a clearance requirement names must also keep its
        gap through the turn, and the least gap through it is recorded."""
        turn = pair.turn
        if turn is None or pair.touching is not None:
            return None
        assert pair.b is not None and pair.ca is not None and pair.cb is not None
        tol = self.world.tolerances
        turned, collider = pair.p.turned(turn, settings.mesh_tolerance_mm + tol.position_mm, tol.position_mm)
        if (turned ^ pair.b).volume() > pair.sitting_mm3 + settings.overlap_mm3:
            return None
        if not pair.tracked:
            return out
        found = min(pair.ca.distance(pair.cb), collider.distance(pair.cb))
        if found < pair.need:
            return None
        return out.model_copy(update={"turned": Least(a=out.a, b=out.b, gap_mm=round(found, 6), kind=self.plan.kind,
                                                      at=None, complete=True)})  # fmt: skip

    def way(self, end: float, pair: _Pair) -> _Way:
        return _Continuous(self, end, pair)


class _Traced(_Motion):
    """A traced plan: its positions, solved and checked."""

    def __init__(self, plan: Plan, traced: Traced) -> None:
        super().__init__(plan)
        self.traced, self.poses = traced, traced.stacked()
        self.digests = {c: hashlib.sha256(np.ascontiguousarray(f).tobytes()).hexdigest() for c, f in self.poses.items()}

    def bounds(self, leaf: Leaf, cluster: str) -> np.ndarray:
        moved = _placed(corners(leaf.bounds), self.poses[cluster]).reshape(-1, 3)
        return np.array([moved.min(axis=0), moved.max(axis=0)])

    def key(self, pair: _Pair) -> list[object]:
        t = self.traced
        return ["traced", t.values, t.rest, self.digests[pair.cp], pair.cq and self.digests[pair.cq]]

    def turn(self, pair: _Pair) -> Axis | None:
        """Where a mate between their clusters holds them to a turn about its axis, its travel staying put."""
        find, tol = self.world.clusters.find, self.world.tolerances
        for k in self.world.moving_links:
            ends = {find(k.a.instance), find(k.b.instance)}
            if ends != {pair.cp, pair.cq or find(pair.q.path)} or not {"origin_xy", "axis"} <= k.source.holds:
                continue
            a, b = (self.poses.get(find(e.instance)) for e in (k.a, k.b))
            n = len(self.traced.values)
            seen = fr.rigid_inverse(_or_still(a, n) @ k.a.frame) @ (_or_still(b, n) @ k.source.facing(k.b.frame))
            if float(np.ptp(seen[:, 2, 3])) <= tol.position_mm:
                return Axis("angle", fr.origin(k.a.frame), fr.z_axis(k.a.frame))
        return None

    def way(self, end: float, pair: _Pair) -> _Way:
        return _Discrete(self, end, pair)


def _or_still(poses: np.ndarray | None, n: int) -> np.ndarray:
    return np.broadcast_to(fr.IDENTITY, (n, 4, 4)) if poses is None else poses


# --------------------------------------------------------------------------- ways


class _Way:
    """One pair's way, from where the input sits towards `end`: its positions, as `start` and what `next` gives, each
    an order along the way."""

    end: float
    start: float
    step_mm: float

    def value(self, at: float) -> float:
        """The input at a position."""

    def motions(self, at: float) -> tuple[np.ndarray | None, np.ndarray | None]: ...

    def travel(self, at: float) -> float:
        """How far the pair's points may have moved against each other since the way's start (mm)."""

    def left(self, at: float) -> float:
        """How far they may move against each other from `at` to the way's end (mm)."""

    def near(self, at: float, found: float) -> bool:
        """Whether a gap of `found` is less than the next position moves them: from then on only whether they meet."""

    def next(self, at: float, due: float) -> float | None:
        """The first position from which the pair has travelled `due` since the start, or None past the end."""

    def budget(self, positions: int) -> bool:
        """Whether the way may take more positions."""
        return True


class _Continuous(_Way):
    def __init__(self, motion: _Exact, end: float, pair: _Pair) -> None:
        self.motion, self.end, self.speed = motion, end, pair.speed
        self.mine, self.theirs = motion.drives[pair.cp], motion.drives.get(pair.cq or "")
        self.sign, self.span = math.copysign(1.0, end - motion.plan.rest), abs(end - motion.plan.rest)
        self.start, self.step_mm = 0.0, motion.world.tolerances.position_mm

    def value(self, at: float) -> float:
        return self.motion.plan.rest + self.sign * at

    def motions(self, at: float) -> tuple[np.ndarray | None, np.ndarray | None]:
        x = self.sign * at
        return self.mine.at(x), None if self.theirs is None else self.theirs.at(x)

    def travel(self, at: float) -> float:
        return self.speed * at

    def left(self, at: float) -> float:
        return self.speed * (self.span - at)

    def near(self, at: float, found: float) -> bool:
        return False

    def next(self, at: float, due: float) -> float | None:
        if at >= self.span or self.speed <= 0:
            return None
        return min(max(due / self.speed, at), self.span)

    def budget(self, positions: int) -> bool:
        return positions < self.motion.world.tolerances.motion_steps


class _Discrete(_Way):
    def __init__(self, motion: _Traced, end: float, pair: _Pair) -> None:
        values, rest = motion.traced.values, motion.traced.rest_index
        self.steps = list(range(rest, len(values))) if end > motion.plan.rest else list(range(rest, -1, -1))
        self.motion, self.end, self.start = motion, values[self.steps[-1]], 0.0
        self.step_mm, self.tracked = motion.world.tolerances.position_mm, pair.tracked
        poses = motion.poses
        self.mine, self.theirs = poses[pair.cp][self.steps], None if pair.cq is None else poses[pair.cq][self.steps]
        self.travels = _travel(corners(pair.p.bounds), _against(self.mine, self.theirs))

    def value(self, at: float) -> float:
        return self.motion.traced.values[self.steps[int(at)]]

    def motions(self, at: float) -> tuple[np.ndarray | None, np.ndarray | None]:
        return self.mine[int(at)], None if self.theirs is None else self.theirs[int(at)]

    def travel(self, at: float) -> float:
        return float(self.travels[int(at)])

    def left(self, at: float) -> float:
        return float(self.travels[-1] - self.travels[int(at)])

    def near(self, at: float, found: float) -> bool:
        t, j = self.travels, int(at)
        return not self.tracked and j + 1 < len(t) and t[j] + found < t[j + 1]

    def next(self, at: float, due: float) -> float | None:
        j = int(at)
        k = j + 1 + int(np.searchsorted(self.travels[j + 1 :], due))
        return float(k) if k < len(self.steps) else None


def _walk(way: _Way, pair: _Pair, settings: AnalysisSettings, plan: Plan) -> _Walked:
    """The pair along the way, measured each time it could have closed its gap (see the module's docstring), up to
    the first position where it meets or parts, or it can no longer meet before the end, or its positions run out."""
    assert pair.a is not None and pair.b is not None and pair.ca is not None and pair.cb is not None
    tol, out = plan.world.tolerances, _Walked()
    at = before = way.start
    need, near = pair.need, False
    while True:
        so_far = way.travel(at)
        mine, theirs = way.motions(at)
        if pair.touching is not None:  # kept in contact: measured every SAMPLE_MM, for parting or overlap
            found = pair.ca.distance(pair.cb, mine, theirs)
            if found > pair.parts_at(settings, tol):
                return out.model_copy(update={"order": at, "parted": Parted(
                    kind=plan.kind, at=way.value(at), end=way.end, a=pair.p.path, b=pair.q.path,
                    gap_mm=round(found, 6), relation=pair.touching.source.address)})  # fmt: skip
            due = so_far + SAMPLE_MM
        else:
            if near:
                found = 0.0 if pair.ca.meets(pair.cb, mine, theirs) else way.step_mm
            else:
                found = pair.ca.distance(pair.cb, mine, theirs)
                if found >= need + way.left(at) + way.step_mm:  # it cannot close the gap before the end
                    return out
                if pair.tracked:
                    out.gaps.append((at, way.value(at), found))
                need = need if found >= need else 0.0  # its clearance is broken: only contact
                near = way.near(at, found)
            due = so_far + max(found - need, way.step_mm)
        if found < way.step_mm and (shared := _shared(pair, mine, theirs)) > pair.sitting_mm3 + settings.overlap_mm3:
            return out.model_copy(update={"order": at, "contact": Contact(
                kind=plan.kind, at=way.value(at), end=way.end, clear=way.value(before), a=pair.p.path, b=pair.q.path,
                overlap_mm3=round(shared, 6))})  # fmt: skip
        following = way.next(at, due)
        if following is None:
            return out
        if not way.budget(len(out.positions)):
            return out.model_copy(update={"order": at, "stop": Stop(kind=plan.kind, at=way.value(at), a=pair.p.path,
                                                                    b=pair.q.path, gap_mm=round(found, 6))})  # fmt: skip
        before, at = at, following
        out.positions.append(at)


def _shared(pair: _Pair, mine: np.ndarray | None, theirs: np.ndarray | None) -> float:
    """What the two share (mm³), each moved."""
    assert pair.a is not None and pair.b is not None
    a = pair.a if mine is None else pair.a.transform(mine[:3, :])
    b = pair.b if theirs is None else pair.b.transform(theirs[:3, :])
    return float((a ^ b).volume())


def _against(mine: np.ndarray, theirs: np.ndarray | None) -> np.ndarray:
    """p's motion against q's, at each position."""
    return mine if theirs is None else fr.rigid_inverse(theirs) @ mine


def _placed(points: np.ndarray, motions: np.ndarray) -> np.ndarray:
    """Points (n × 3) moved by each of a stack of motions: positions × n × 3."""
    return np.einsum("sij,nj->sni", motions[:, :3, :3], points) + motions[:, None, :3, 3]


def _travel(points: np.ndarray, motions: np.ndarray) -> np.ndarray:
    """How far points have moved, summed over the motions so far, at each one."""
    steps = np.linalg.norm(np.diff(_placed(points, motions), axis=0), axis=2).max(axis=1)
    return np.concatenate([[0.0], np.cumsum(steps)])
