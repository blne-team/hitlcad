"""An input's sweep: every pair its motion may bring together, measured from where the input sits towards each end
of its range (both ways round, for a full turn), up to the first contact each way.

Each way advances conservatively: a pair's gap is measured, and measured again no sooner than its parts could have
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

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from pydantic import Field

from hitlcad.analysis.base import ResultCache
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

if TYPE_CHECKING:
    import manifold3d


class Swept(Findings):
    """What a plan's sweep found, and for a traced plan its positions and what a watch of it is of (`key`)."""

    positions: int = 0
    stopped: list[Stop] = Field(default_factory=list)
    traced: Traced | None = None
    key: str = ""


def sweep(plan: Plan, settings: AnalysisSettings, cache: ResultCache) -> Swept:
    """The plan swept through its range: once per plan, and cached by what it depends on."""
    if plan.swept is None:
        plan.swept = _sweep(plan, settings, cache)
    return plan.swept


@dataclass
class _Pair(Pair):
    """A pair, the clusters each moves with (`cq` None: q stays), and once shaped, the solids moved (placed where they
    sit), their colliders, and on an exact way how fast the gap between them can close (mm per unit of the input)."""

    cp: str
    cq: str | None
    a: manifold3d.Manifold | None = None
    b: manifold3d.Manifold | None = None
    ca: Collider | None = None
    cb: Collider | None = None
    speed: float = 0.0

    def key(self) -> list[object]:
        """What its sweep depends on, once `at_rest` has kept it, but the motion (the plan's)."""
        return [self.p.path, self.p.part.digest, np.round(self.p.transform, 6).tolist(), self.cp, self.q.path,
                self.q.part.digest, np.round(self.q.transform, 6).tolist(), self.cq, self.need, self.tracked,
                round(self.sitting_mm3, 6), self.contact_key()]  # fmt: skip


def _sweep(plan: Plan, settings: AnalysisSettings, cache: ResultCache) -> Swept:
    traced = None if plan.exact else positions(plan, settings, cache)
    if traced is not None and (traced.why or traced.held or len(traced.values) < 2):
        return Swept(traced=traced)
    motion: _Motion = _Exact(plan) if traced is None else _Traced(plan, traced)
    settled = Swept()
    pairs = at_rest(motion.pairs(), plan.world, settings, cache, settled)
    tol = plan.world.tolerances
    key = cache.key("sweep", motion.key(), [p.key() for p in pairs], settings.overlap_mm3, settings.mesh_tolerance_mm,
                    tol.position_mm, tol.motion_steps)  # fmt: skip
    swept = Swept.model_validate(cache.get_or(key, lambda: _collide(plan, motion, pairs, settings).model_dump()))
    return swept.model_copy(update={
        "resting": sorted(set(settled.resting)), "unmeasurable": sorted(set(settled.unmeasurable)), "traced": traced,
        "key": key if traced is not None else "",
        "least": [least.model_copy(update={"complete": least.complete and not settled.unmeasurable})
                  for least in swept.least],
    })  # fmt: skip


def _collide(plan: Plan, motion: _Motion, pairs: list[_Pair], settings: AnalysisSettings) -> Swept:
    out, least = Swept(), {}
    kept = [p for p in pairs if motion.shape(p, settings) and not motion.clear_in_a_turn(p, settings, least)]
    for end in plan.ends():
        if out.stopped:
            break
        _advance(motion.way(end, kept), kept, settings, plan, out, least)
    out.least = [v.model_copy(update={"complete": v.complete and not out.stopped}) for v in least.values()]
    return out


# --------------------------------------------------------------------------- the motion, as a sweep sees it


class _Motion:
    """How a plan's clusters move, for a sweep."""

    def __init__(self, plan: Plan) -> None:
        self.plan, self.world = plan, plan.world
        self.round_mm = 0.0

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
                    out.append(_Pair(w.leaves[p], other, need or 0.0, need is not None, cp, cq))
        return out

    def bounds(self, leaf: Leaf, cluster: str) -> np.ndarray: ...

    def key(self) -> list[object]: ...

    def turn(self, pair: _Pair) -> Axis | None:
        """The axis p only turns about against q, if it does."""

    def shape(self, pair: _Pair, settings: AnalysisSettings) -> bool:
        """Give the pair the solids the sweep moves; False when nothing of them can meet. Of a pair turning against
        each other about an axis, only what is not round about it of each is moved (`not_round`)."""
        round_mm = settings.mesh_tolerance_mm + self.world.tolerances.position_mm  # a facetted round is round to this
        turn = self.turn(pair)
        if turn is None:
            (pair.a, pair.ca), (pair.b, pair.cb) = (
                (pair.p.manifold, pair.p.collider),
                (pair.q.manifold, pair.q.collider),
            )
            return True
        (pair.a, pair.ca), (pair.b, pair.cb) = pair.p.not_round(turn, round_mm), pair.q.not_round(turn, round_mm)
        if pair.ca is None or pair.cb is None:  # all of one is round about the turn: it keeps its gap
            return False
        pair.sitting_mm3 = float((pair.a ^ pair.b).volume()) if pair.sitting_mm3 else 0.0
        return True

    def clear_in_a_turn(self, pair: _Pair, settings: AnalysisSettings, least: dict[tuple[str, str], Least]) -> bool:
        return False

    def way(self, end: float, pairs: list[_Pair]) -> _Way: ...


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

    def key(self) -> list[object]:
        return ["exact", self.plan.kind, self.plan.rest, self.plan.ends(), {c: d.key() for c, d in self.drives.items()}]

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

    def clear_in_a_turn(self, pair: _Pair, settings: AnalysisSettings, least: dict[tuple[str, str], Least]) -> bool:
        """Whether nothing the moving solid passes through in a full turn meets the other (which stays put), so that
        no position of any range about the axis can; a pair a clearance requirement names must also keep its gap
        through the turn, and the least gap through it is recorded."""
        turn = self.turn(pair)
        if turn is None or pair.touching is not None:
            return False
        assert pair.b is not None and pair.ca is not None and pair.cb is not None
        tol = self.world.tolerances
        turned, collider = pair.p.turned(turn, settings.mesh_tolerance_mm + tol.position_mm, tol.position_mm)
        if (turned ^ pair.b).volume() > pair.sitting_mm3 + settings.overlap_mm3:
            return False
        if not pair.tracked:
            return True
        found = min(pair.ca.distance(pair.cb), collider.distance(pair.cb))
        if found < pair.need:
            return False
        _record(least, pair, found, self.plan.kind, None, complete=True)
        return True

    def way(self, end: float, pairs: list[_Pair]) -> _Way:
        return _Continuous(self, end)


class _Traced(_Motion):
    """A traced plan: its positions, solved and checked."""

    def __init__(self, plan: Plan, traced: Traced) -> None:
        super().__init__(plan)
        self.traced, self.poses = traced, traced.stacked()

    def bounds(self, leaf: Leaf, cluster: str) -> np.ndarray:
        moved = _placed(corners(leaf.bounds), self.poses[cluster]).reshape(-1, 3)
        return np.array([moved.min(axis=0), moved.max(axis=0)])

    def key(self) -> list[object]:
        return ["traced", self.traced.key]

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

    def way(self, end: float, pairs: list[_Pair]) -> _Way:
        return _Discrete(self, end, pairs)


def _or_still(poses: np.ndarray | None, n: int) -> np.ndarray:
    return np.broadcast_to(fr.IDENTITY, (n, 4, 4)) if poses is None else poses


# --------------------------------------------------------------------------- ways


class _Way:
    """One way of a sweep, from where the input sits towards `end`: its positions, as `start` and what `next` gives."""

    end: float
    start: object
    step_mm: float

    def value(self, at: object) -> float: ...

    def motions(self, pair: _Pair, at: object) -> tuple[np.ndarray | None, np.ndarray | None]: ...

    def travel(self, pair: _Pair, at: object) -> float:
        """How far the pair's points may have moved against each other since the way's start (mm)."""

    def left(self, pair: _Pair, at: object) -> float:
        """How far they may move against each other from `at` to the way's end (mm)."""

    def near(self, pair: _Pair, at: object, found: float) -> bool:
        """Whether a gap of `found` is less than the next position moves them: from then on only whether they meet."""

    def next(self, at: object, due: dict[int, float], pairs: list[_Pair]) -> object | None:
        """The next position any pair is due at (`due`: by the travel each is next measured at), or None."""

    def budget(self, positions: int) -> bool:
        """Whether the way may take more positions."""
        return True


class _Continuous(_Way):
    def __init__(self, motion: _Exact, end: float) -> None:
        self.motion, self.end = motion, end
        self.sign, self.span = math.copysign(1.0, end - motion.plan.rest), abs(end - motion.plan.rest)
        self.start, self.step_mm = 0.0, motion.world.tolerances.position_mm

    def value(self, at: object) -> float:
        return self.motion.plan.rest + self.sign * float(at)  # type: ignore[arg-type]

    def motions(self, pair: _Pair, at: object) -> tuple[np.ndarray | None, np.ndarray | None]:
        dq = self.motion.drives.get(pair.cq or "")
        x = self.sign * float(at)  # type: ignore[arg-type]
        return self.motion.drives[pair.cp].at(x), None if dq is None else dq.at(x)

    def travel(self, pair: _Pair, at: object) -> float:
        return pair.speed * float(at)  # type: ignore[arg-type]

    def left(self, pair: _Pair, at: object) -> float:
        return pair.speed * (self.span - float(at))  # type: ignore[arg-type]

    def near(self, pair: _Pair, at: object, found: float) -> bool:
        return False

    def next(self, at: object, due: dict[int, float], pairs: list[_Pair]) -> object | None:
        x = float(at)  # type: ignore[arg-type]
        following = min((due[id(p)] / p.speed for p in pairs if p.speed > 0), default=math.inf)
        if x >= self.span or following == math.inf:
            return None
        return min(max(following, x), self.span)

    def budget(self, positions: int) -> bool:
        return positions < self.motion.world.tolerances.motion_steps


class _Discrete(_Way):
    def __init__(self, motion: _Traced, end: float, pairs: list[_Pair]) -> None:
        values, rest = motion.traced.values, motion.traced.rest_index
        self.steps = list(range(rest, len(values))) if end > motion.plan.rest else list(range(rest, -1, -1))
        self.motion, self.end, self.start = motion, values[self.steps[-1]], 0
        self.step_mm = motion.world.tolerances.position_mm
        poses = motion.poses
        self.moves = {
            id(p): (poses[p.cp][self.steps], None if p.cq is None else poses[p.cq][self.steps]) for p in pairs
        }
        self.travels = {id(p): _travel(corners(p.p.bounds), _against(*self.moves[id(p)])) for p in pairs}

    def value(self, at: object) -> float:
        return self.motion.traced.values[self.steps[int(at)]]  # type: ignore[call-overload]

    def motions(self, pair: _Pair, at: object) -> tuple[np.ndarray | None, np.ndarray | None]:
        mine, theirs = self.moves[id(pair)]
        return mine[int(at)], None if theirs is None else theirs[int(at)]  # type: ignore[call-overload]

    def travel(self, pair: _Pair, at: object) -> float:
        return float(self.travels[id(pair)][int(at)])  # type: ignore[call-overload]

    def left(self, pair: _Pair, at: object) -> float:
        t = self.travels[id(pair)]
        return float(t[-1] - t[int(at)])  # type: ignore[call-overload]

    def near(self, pair: _Pair, at: object, found: float) -> bool:
        t, j = self.travels[id(pair)], int(at)  # type: ignore[call-overload]
        return not pair.tracked and j + 1 < len(t) and t[j] + found < t[j + 1]

    def next(self, at: object, due: dict[int, float], pairs: list[_Pair]) -> object | None:
        j = int(at)  # type: ignore[call-overload]
        found = [j + 1 + int(np.searchsorted(self.travels[id(p)][j + 1 :], due[id(p)])) for p in pairs
                 if due[id(p)] != math.inf]  # fmt: skip
        k = min(found, default=len(self.steps))
        return k if k < len(self.steps) else None


def _advance(way: _Way, pairs: list[_Pair], settings: AnalysisSettings, plan: Plan, out: Swept,
             least: dict[tuple[str, str], Least]) -> None:  # fmt: skip
    """The way, position by position, each pair measured where it is due (see the module's docstring), up to the
    first position where any meets, parts, or the positions run out."""
    tol, at, before = plan.world.tolerances, way.start, way.start
    due = {id(p): 0.0 for p in pairs}
    need = {id(p): p.need for p in pairs}
    near: set[int] = set()
    gaps: dict[int, float] = {}
    positions = 0
    while True:
        hits: list[Contact] = []
        parted: list[Parted] = []
        for p in pairs:
            so_far = way.travel(p, at)
            if due[id(p)] == math.inf or so_far < due[id(p)] - 1e-9:  # not yet due, to rounding
                continue
            assert p.a is not None and p.b is not None and p.ca is not None and p.cb is not None
            mine, theirs = way.motions(p, at)
            if p.touching is not None:  # kept in contact: measured every SAMPLE_MM, for parting or overlap
                found = p.ca.distance(p.cb, mine, theirs)
                due[id(p)] = so_far + SAMPLE_MM
                if found > p.parts_at(settings, tol):
                    parted.append(Parted(kind=plan.kind, at=way.value(at), end=way.end, a=p.p.path, b=p.q.path,
                                         gap_mm=round(found, 6), relation=p.touching.source.address))  # fmt: skip
                    continue
            elif id(p) in near:
                found = 0.0 if p.ca.meets(p.cb, mine, theirs) else way.step_mm
            else:
                found = p.ca.distance(p.cb, mine, theirs)
                if found >= need[id(p)] + way.left(p, at) + way.step_mm:  # it cannot close the gap before the end
                    due[id(p)] = math.inf
                    continue
                if p.tracked:
                    _record(least, p, found, plan.kind, way.value(at), complete=plan.exact)
                need[id(p)] = need[id(p)] if found >= need[id(p)] else 0.0  # its clearance is broken: only contact
                if way.near(p, at, found):
                    near.add(id(p))
            gaps[id(p)] = found
            if found < way.step_mm and (shared := _shared(p, mine, theirs)) > p.sitting_mm3 + settings.overlap_mm3:
                hits.append(Contact(kind=plan.kind, at=way.value(at), end=way.end, clear=way.value(before),
                                    a=p.p.path, b=p.q.path, overlap_mm3=round(shared, 6)))  # fmt: skip
            if p.touching is None:
                due[id(p)] = so_far + max(found - need[id(p)], way.step_mm)
        if hits or parted:
            out.contacts += hits
            out.parted += parted
            return
        following = way.next(at, due, pairs)
        if following is None:
            return
        if not way.budget(positions):
            closest = min(pairs, key=lambda p: due[id(p)])
            out.stopped.append(Stop(kind=plan.kind, at=way.value(at), a=closest.p.path, b=closest.q.path,
                                    gap_mm=round(gaps.get(id(closest), 0.0), 6)))  # fmt: skip
            return
        before, at = at, following
        positions += 1
        out.positions += 1


def _shared(pair: _Pair, mine: np.ndarray | None, theirs: np.ndarray | None) -> float:
    """What the two share (mm³), each moved."""
    assert pair.a is not None and pair.b is not None
    a = pair.a if mine is None else pair.a.transform(mine[:3, :])
    b = pair.b if theirs is None else pair.b.transform(theirs[:3, :])
    return float((a ^ b).volume())


def _record(least: dict[tuple[str, str], Least], pair: _Pair, found: float, kind: str, at: float | None,
            complete: bool) -> None:  # fmt: skip
    key = (pair.p.path, pair.q.path)
    if key not in least or found < least[key].gap_mm:
        least[key] = Least(a=pair.p.path, b=pair.q.path, gap_mm=round(found, 6), kind=kind,
                           at=None if at is None else round(at, 6), complete=complete)  # fmt: skip


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
