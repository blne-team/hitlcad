"""Motion: every mate with a range of motion (its `limits`, or a full turn for a revolute without limits)
is moved through it, and nothing may collide on the way.

The parts that move are the side of the mate away from `ground`: rigid mates, fastener sets and rigid
components join instances into rigid clusters, and the other mates join the clusters. Relations carry
the motion on: a gear driven by a moving gear turns about its own axis by the relation's ratio. A mate
inside a closed loop of mates moves only together with the rest of the loop, which takes a solver;
hitlcad checks rather than solves, so it reports such a mate's motion as unverified.

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
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from pydantic import Field

from hitlcad.analysis.base import Context, ResultCache, label, result
from hitlcad.analysis.coverage import Link, nested_links
from hitlcad.analysis.features import Unmeasurable
from hitlcad.analysis.interference import Allowance, allowed, beyond, boxes_touch, overlap
from hitlcad.analysis.model import AnalysisSettings, BuiltDesign, Leaf, StructureError, gap
from hitlcad.core import frames as fr
from hitlcad.core.checks import ClearanceCheck
from hitlcad.core.report import Result
from hitlcad.core.structure import Axis, Tolerances, _Mate, _Relation
from hitlcad.core.types import Model

if TYPE_CHECKING:
    import manifold3d

SEARCH_MM = 1.0  # how far past what a pair must keep its first gap measurement looks; widened when nothing is there
_UNIT = 0.01  # of the driving motion, to read a driven turn by: `Axis.of` reads turns within ±180°


@dataclass
class Plan:
    """One mate's sweep: its free motions and ranges, which side moves, and the world it moves in."""

    link: Link
    ranges: list[tuple[str, float, float, bool]]  # (free motion, start, end, continuous)
    moving: set[str]  # clusters
    sign: float
    free: dict[str, float]
    world: World
    swept: Swept | None = None  # once `sweep` has run


@dataclass
class World:
    leaves: dict[str, Leaf]
    clusters: _Clusters
    ground: str
    moving_links: list[Link]
    relations: list[Link]
    ok_pairs: dict[frozenset[str], Allowance]
    tolerances: Tolerances
    tracked: dict[frozenset[str], float] = field(
        metadata={"doc": "Leaf pairs a clearance requirement names, and the least gap it needs (0: no minimum)"}
    )

    def leaves_of(self, clusters: set[str]) -> list[str]:
        return [p for p in self.leaves if self.clusters.find(p) in clusters]


@dataclass(frozen=True)
class Drive:
    """How a cluster moves per unit of the mate's free motion: `rate` times as far about or along `axis`."""

    axis: Axis
    rate: float

    def at(self, value: float) -> fr.Frame:
        return self.axis.motion(self.rate * value)

    def speed(self, radius: float) -> float:
        """The furthest a point `radius` from the axis travels per unit (mm)."""
        return abs(self.rate) * (1.0 if self.axis.kind == "travel" else math.radians(1.0) * radius)

    def key(self) -> list[float | str]:
        return [self.axis.kind, *np.round(self.axis.origin, 6).tolist(), *np.round(self.axis.direction, 6).tolist(),
                round(self.rate, 9)]  # fmt: skip


class Contact(Model):
    """The first position, moving from where the mate sits towards `end`, at which two instances overlap."""

    kind: str
    at: float
    end: float
    clear: float = Field(description="The last position checked clear before it")
    a: str
    b: str
    overlap_mm3: float


class Stop(Model):
    """Where a direction was given up: two instances stay too close for the positions left."""

    kind: str
    at: float
    a: str
    b: str
    gap_mm: float


class Least(Model):
    """The least gap a pair a clearance requirement names comes to, and where: `at` None is somewhere in a
    full turn (a pair cleared at once, see the module's docstring)."""

    a: str
    b: str
    gap_mm: float
    kind: str
    at: float | None
    complete: bool = Field(description="Every range was swept to its ends")


class Swept(Model):
    """What a plan's sweep found."""

    positions: int = 0
    stuck: list[str] = Field(default_factory=list, description="Relations coupling it to parts that cannot move")
    contacts: list[Contact] = Field(default_factory=list)
    stopped: list[Stop] = Field(default_factory=list)
    resting: list[tuple[str, str, float]] = Field(default_factory=list, description="Pairs overlapping where they sit")
    unmeasurable: list[tuple[str, str, str]] = Field(default_factory=list)
    least: list[Least] = Field(default_factory=list)


def _ranges(mate: _Mate) -> list[tuple[str, float, float, bool]]:
    limits = mate.limits_of()
    out = []
    for kind in mate.free_motion:
        bounds = getattr(limits, kind) if limits else None
        if bounds:
            out.append((kind, bounds[0], bounds[1], False))
        elif kind == "angle":
            out.append((kind, 0.0, 360.0, True))
    return out


def _tracked(built: BuiltDesign) -> dict[frozenset[str], float]:
    """Leaf pairs the clearance requirements name, and the least gap each needs."""
    out: dict[frozenset[str], float] = {}
    for req in built.spec.requirements:
        check = req.check
        if not isinstance(check, ClearanceCheck):
            continue
        try:
            a, b = (built.leaves_of(name) for name in check.between)
            need = float(built.nominal(check.min) if isinstance(check.min, str) else check.min or 0.0)
        except (StructureError, KeyError):  # the requirement reports it
            continue
        for la in a:
            for lb in b:
                key = frozenset((la.path, lb.path))
                out[key] = max(out.get(key, 0.0), need)
    return out


def plans(built: BuiltDesign) -> list[Plan | Result]:
    """A plan for every mate with a range of motion, or the result saying why it does not move on its own."""
    leaves = {leaf.path: leaf for leaf in built.leaves()}
    if not leaves or built.spec.ground is None:
        return []
    all_links, rigid_groups = nested_links(built, lambda design, name: design.spec.components[name].flexible)
    clusters = _Clusters(leaves)
    for group in rigid_groups:
        clusters.union_all(group)
    for link in all_links:
        if link.rigid:
            clusters.union(link.a.instance, link.b.instance)
    try:
        ground = clusters.find(built.leaves_of(built.spec.ground)[0].path)
    except LookupError:
        return []
    moving_links = [k for k in all_links if isinstance(k.source, _Mate) and not k.rigid]
    world = World(leaves, clusters, ground, moving_links, [k for k in all_links if isinstance(k.source, _Relation)],
                  allowed(all_links), built.tolerances, _tracked(built))  # fmt: skip
    out: list[Plan | Result] = []
    for link in moving_links:
        mate: _Mate = link.source
        ranges = _ranges(mate)
        if not ranges:
            continue
        about = [link.a.instance, link.b.instance, mate.a, mate.b]
        if clusters.find(link.a.instance) == clusters.find(link.b.instance):
            out.append(result(NAME, mate.address, True, f"{label(link.a)} ↔ {label(link.b)}: held by rigid mates or "
                              "fasteners as well, so it does not move", about, severity="info"))  # fmt: skip
            continue
        side_b = clusters.side(link.b.instance, moving_links, exclude=link, ground=ground)
        side_a = clusters.side(link.a.instance, moving_links, exclude=link, ground=ground)
        if side_b is None and side_a is None:
            out.append(result(NAME, mate.address, None, f"{label(link.a)} ↔ {label(link.b)}: in a closed loop of "
                              "mates, it moves only with the rest of the loop; not analysed without a solver", about))  # fmt: skip
            continue
        moving, sign = (side_b, 1.0) if side_b is not None else (side_a, -1.0)
        assert moving is not None
        free = mate.verify(link.a.frame, mate.facing(link.b.frame), "axial", built.tolerances).free
        out.append(Plan(link, ranges, moving, sign, free, world))
    return out


def sweep(plan: Plan, settings: AnalysisSettings, cache: ResultCache) -> Swept:
    """The plan swept through each of its ranges: once per plan, and cached by what it depends on."""
    if plan.swept is None:
        plan.swept = _Sweep(plan, settings, cache).run()
    return plan.swept


NAME = "motion"


class Motion:
    name = NAME

    def run(self, ctx: Context) -> list[Result]:
        return [p if isinstance(p, Result) else self._verdict(p, sweep(p, ctx.settings, ctx.cache))
                for p in ctx.built.motion_plans]  # fmt: skip

    def _verdict(self, plan: Plan, swept: Swept) -> Result:
        link = plan.link
        mate: _Mate = link.source
        about = [link.a.instance, link.b.instance, mate.a, mate.b]
        pair = f"{label(link.a)} ↔ {label(link.b)}"
        if swept.stuck:
            return result(self.name, mate.address, None, f"{pair}: its motion is coupled by {', '.join(swept.stuck)} "
                          "to parts that cannot move with it here (a closed loop or the ground); not analysed without "
                          "a solver", about)  # fmt: skip
        if swept.contacts:
            first = min(swept.contacts, key=lambda c: abs(c.at - plan.free.get(c.kind, 0.0)))
            return result(self.name, mate.address, False, f"{pair}: {self._contacts(plan, swept.contacts)}",
                          [*about, *(p for c in swept.contacts for p in (c.a, c.b))],
                          {first.kind: first.at, "overlap_mm3": first.overlap_mm3})  # fmt: skip
        ranges = ", ".join("a full turn" if continuous else f"{lo:g} … {hi:g}{_unit(kind)}"
                           for kind, lo, hi, continuous in plan.ranges)  # fmt: skip
        problems = [f"{s.a} and {s.b} stay {s.gap_mm:.3f} mm apart from {s.at:.2f}{_unit(s.kind)} on, too close to "
                    f"sweep further in {plan.world.tolerances.motion_steps} positions: give them clearance"
                    for s in swept.stopped]  # fmt: skip
        problems += [f"{a} against {b} is not measurable: {why}" for a, b, why in swept.unmeasurable]
        problems += [f"{a} and {b} already overlap by {mm3:.2f} mm³ where they sit (see interference), so their "
                     "motion is not checked" for a, b, mm3 in swept.resting]  # fmt: skip
        if problems:
            return result(self.name, mate.address, None, f"{pair} moves through {ranges} without collision as far as "
                          f"it was checked; " + "; ".join(problems),
                          [*about, *(p for s in swept.stopped for p in (s.a, s.b)),
                           *(p for a, b, *_ in [*swept.unmeasurable, *swept.resting] for p in (a, b))])  # fmt: skip
        checked = f"{swept.positions} positions checked" if swept.positions else "nothing in its reach meets its sweep"
        return result(self.name, mate.address, True, f"{pair} moves through {ranges} without collision ({checked})",
                      about, {"positions": swept.positions})  # fmt: skip

    @staticmethod
    def _contacts(plan: Plan, contacts: list[Contact]) -> str:
        """Where it first hits something on the way from where it sits towards each end, and what to change."""
        out = []
        for kind, lo, hi, continuous in plan.ranges:
            found = [c for c in contacts if c.kind == kind]
            if not found:
                continue
            unit, rest = _unit(kind), plan.free.get(kind, 0.0)
            hits = " and ".join(f"{c.a} hits {c.b} at {c.at:.1f}{unit} on the way to {c.end:g}{unit}" for c in found)
            others = ", ".join(sorted({c.b for c in found}))
            if continuous:
                out.append(f"from {rest:.1f}{unit}, {hits}: it cannot turn a full turn; clear {others}, or give the "
                           f"mate limits.{kind}")  # fmt: skip
                continue
            ends = [lo, hi]
            for c in found:
                up = c.end > rest
                ends[up] = (math.floor if up else math.ceil)(c.clear * 10) / 10  # rounded towards where it sits
            out.append(f"from {rest:.1f}{unit}, {hits}: limits.{kind} [{lo:g}, {hi:g}] run past it; make them "
                       f"[{ends[0]:g}, {ends[1]:g}], or clear {others}")  # fmt: skip
        return "; ".join(out)


def _unit(kind: str) -> str:
    return "°" if kind == "angle" else " mm"


# --------------------------------------------------------------------------- sweeping


@dataclass
class _Pair:
    """A moving leaf and another its motion may bring it near, the gap a clearance requirement needs them to
    keep, and once shaped, the solids the sweep moves (placed where they sit) and how fast the gap between
    them can close (mm per unit of the mate's motion)."""

    p: Leaf
    q: Leaf
    dp: Drive
    dq: Drive | None
    need: float
    tracked: bool
    a: manifold3d.Manifold | None = None
    b: manifold3d.Manifold | None = None
    speed: float = 0.0
    sitting_mm3: float = 0.0  # what they share where they sit, as a mate allows: more than that is a contact

    def solids(self, value: float) -> tuple[manifold3d.Manifold, manifold3d.Manifold]:
        assert self.a is not None and self.b is not None
        b = self.b if self.dq is None else self.b.transform(self.dq.at(value)[:3, :])
        return self.a.transform(self.dp.at(value)[:3, :]), b

    def key(self) -> list[object]:
        return [self.p.path, self.p.part.digest, np.round(self.p.transform, 6).tolist(), self.dp.key(), self.q.path,
                self.q.part.digest, np.round(self.q.transform, 6).tolist(), self.dq.key() if self.dq else None,
                self.need, self.tracked]  # fmt: skip


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
        prepared = []
        for kind, lo, hi, continuous in self.plan.ranges:
            drives, stuck = _drives(self.plan, kind)
            if stuck:
                return Swept(stuck=sorted(stuck))
            rest = self.plan.free.get(kind, 0.0)
            ends = (
                [rest + 180.0, rest - 180.0]
                if continuous
                else [*([hi] if hi > rest else []), *([lo] if lo < rest else [])]
            )
            prepared.append((kind, rest, ends, self._pairs(drives, [end - rest for end in ends])))
        key = self.cache.key("motion", [[kind, rest, ends, [p.key() for p in pairs]] for kind, rest, ends, pairs in prepared],
                             self.settings.overlap_mm3, self.settings.mesh_tolerance_mm, self.tol.position_mm,
                             self.tol.motion_steps)  # fmt: skip
        return Swept.model_validate(self.cache.get_or(key, lambda: self._swept(prepared).model_dump()))

    def _swept(self, prepared: list[tuple[str, float, list[float], list[_Pair]]]) -> Swept:
        out = Swept()
        for kind, rest, ends, candidates in prepared:
            pairs = [
                p for p in self._at_rest(candidates, out) if self._shaped(p) and not self._clear_in_a_turn(p, kind)
            ]
            for end in ends:
                if not out.stopped:
                    self._advance(kind, rest, end, pairs, out)
        complete = not (out.stopped or out.unmeasurable)
        out.least = [least.model_copy(update={"complete": complete}) for least in self.least.values()]
        out.resting = sorted(set(out.resting))
        out.unmeasurable = sorted(set(out.unmeasurable))
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
                    out.append(_Pair(w.leaves[p], other, dp, dq, need or 0.0, need is not None))
        return out

    def _at_rest(self, pairs: list[_Pair], out: Swept) -> list[_Pair]:
        """The pairs to sweep: not those overlapping where they sit beyond what a mate allows (interference
        reports them), nor those that cannot be measured."""
        keep = []
        for pair in pairs:
            try:
                shared = overlap(self.cache, pair.p, fr.IDENTITY, pair.q, fr.IDENTITY)
            except Unmeasurable as e:
                out.unmeasurable.append((pair.p.path, pair.q.path, str(e)))
                continue
            allowance = self.plan.world.ok_pairs.get(frozenset((pair.p.path, pair.q.path)))
            if shared <= self.settings.overlap_mm3 or (
                allowance and not beyond(allowance, pair.p, pair.q, shared, self.settings)
            ):
                pair.sitting_mm3 = shared if shared > self.settings.overlap_mm3 else 0.0
                keep.append(pair)
            else:
                out.resting.append((pair.p.path, pair.q.path, round(shared, 6)))
        return keep

    def _shaped(self, pair: _Pair) -> bool:
        """Give the pair the solids the sweep moves; False when nothing of them can meet. A leaf turning past
        one that stays put moves only its part that is not round about the axis against the other's part
        that is not (`_not_round`): what is round of either turns within itself, and its gap to the other
        stays what it is where they sit. Two moving leaves are moved whole."""
        if pair.dq is not None:
            pair.a, pair.b = pair.p.manifold, pair.q.manifold
            pair.speed = sum(
                d.speed(_radius(s, _axis_frame(d.axis))) for d, s in ((pair.dp, pair.a), (pair.dq, pair.b))
            )
            return True
        (pair.a, radius), (pair.b, _) = self._shape(pair.p, pair.dp), self._shape(pair.q, pair.dp)
        pair.speed = pair.dp.speed(radius)
        if pair.a.is_empty() or pair.b.is_empty():
            return False
        pair.sitting_mm3 = float((pair.a ^ pair.b).volume()) if pair.sitting_mm3 else 0.0
        return True

    def _shape(self, leaf: Leaf, drive: Drive) -> tuple[manifold3d.Manifold, float]:
        """The leaf less what is round about the drive's axis (all of it, along a travel), and how far that
        reaches from the axis."""
        key = (leaf.path, *drive.key())
        if key not in self.shapes:
            frame = _axis_frame(drive.axis)
            solid = _not_round(leaf.manifold, frame, self.round_mm) if drive.axis.kind == "angle" else leaf.manifold
            self.shapes[key] = (solid, _radius(solid, frame))
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
            frame = _axis_frame(pair.dp.axis)
            self.turned[key] = _revolve(_profile(pair.a, frame, 0.0), frame, self.tol.position_mm)
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
                    lower, t.search = t.search, min(t.search * 4, reach)
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


def _drives(plan: Plan, kind: str) -> tuple[dict[str, Drive], set[str]]:
    """Each moving cluster's drive per unit of the mate's `kind`, carried on through relations along the
    axis each gives the cluster it drives; and the relations that couple a moving cluster to one that
    cannot move."""
    w, fa = plan.world, plan.link.a.frame
    drives = dict.fromkeys(plan.moving, Drive(Axis(kind, fr.origin(fa), fr.z_axis(fa)), plan.sign))  # `_Mate.motion`
    changed = True
    while changed:
        changed = False
        for rel in w.relations:
            ca, cb = w.clusters.find(rel.a.instance), w.clusters.find(rel.b.instance)
            if (ca in drives) == (cb in drives):
                continue
            spec: _Relation = rel.source
            ratio = spec.ratio(rel.a.owner.nominals(rel.a.spec), rel.b.owner.nominals(rel.b.spec))
            axis_a, axis_b = spec.axes(rel.a.frame, rel.b.frame)
            if ca in drives:
                driven, drive = rel.b, Drive(axis_b, ratio * axis_a.of(drives[ca].at(_UNIT)) / _UNIT)
            else:
                driven, drive = rel.a, Drive(axis_a, axis_b.of(drives[cb].at(_UNIT)) / _UNIT / ratio)
            own = [k for k in w.moving_links if k is not plan.link and w.clusters.find(driven.instance) in
                   (w.clusters.find(k.a.instance), w.clusters.find(k.b.instance))]  # fmt: skip
            for k in own:
                side = w.clusters.side(driven.instance, w.moving_links, exclude=k, ground=w.ground, also=plan.link)
                if side is None or side & drives.keys():
                    continue
                drives.update(dict.fromkeys(side, drive))
                changed = True
                break
    stuck = {r.source.address for r in w.relations
             if (w.clusters.find(r.a.instance) in drives) != (w.clusters.find(r.b.instance) in drives)}  # fmt: skip
    return drives, stuck


def _swept_bounds(leaf: Leaf, drive: Drive, deltas: list[float]) -> np.ndarray:
    """A box around everything the leaf passes through: moved by each of `deltas` along a travel, anywhere in
    a full turn about an axis."""
    z = drive.axis.direction / np.linalg.norm(drive.axis.direction)
    if drive.axis.kind == "travel":
        shifts = np.outer([drive.rate * d for d in deltas], z)
        return np.array([leaf.bounds[0] + shifts.min(axis=0), leaf.bounds[1] + shifts.max(axis=0)])
    d = leaf.vertices - drive.axis.origin
    along = d @ z
    radius = float(np.linalg.norm(d - np.outer(along, z), axis=1).max())
    ends = np.array([drive.axis.origin + along.min() * z, drive.axis.origin + along.max() * z])
    spread = radius * np.sqrt(np.clip(1 - z * z, 0, 1))
    return np.array([ends.min(axis=0) - spread, ends.max(axis=0) + spread])


# --------------------------------------------------------------------------- turning, seen in profile


def _axis_frame(axis: Axis) -> np.ndarray:
    """The axis' own frame (3 × 4): X and Y across it, Z along it, the origin on it."""
    z = axis.direction / np.linalg.norm(axis.direction)
    u = np.cross(z, [1.0, 0.0, 0.0] if abs(z[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    return np.column_stack([u, np.cross(z, u), z, axis.origin])


def _local(solid: manifold3d.Manifold, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The solid's vertices in the axis' frame, and its triangles."""
    mesh = solid.to_mesh()
    return (np.asarray(mesh.vert_properties[:, :3], dtype=np.float64) - frame[:, 3]) @ frame[:, :3], mesh.tri_verts


def _radius(solid: manifold3d.Manifold, frame: np.ndarray) -> float:
    """How far the solid reaches from the axis."""
    vertices, _ = _local(solid, frame)
    return float(np.linalg.norm(vertices[:, :2], axis=1).max()) if len(vertices) else 0.0


def _profile(solid: manifold3d.Manifold, frame: np.ndarray, pad: float) -> manifold3d.CrossSection:
    """In the plane of distance from the axis (X) and position along it (Y), the region each of the solid's
    triangles spans: a little more than its surface passes through in a turn. A triangle square to the axis
    turns within its own plane and spans no region, unless `pad` gives it that much height either way."""
    import manifold3d

    vertices, faces = _local(solid, frame)
    triangles = vertices[faces]
    outer, inner = np.linalg.norm(triangles[..., :2], axis=-1).max(axis=1), _distance_to_origin(triangles[..., :2])
    lo, hi = triangles[..., 2].min(axis=1) - pad, triangles[..., 2].max(axis=1) + pad
    rects = np.stack([np.stack(c, axis=-1) for c in ((inner, lo), (outer, lo), (outer, hi), (inner, hi))], axis=1)
    return manifold3d.CrossSection(list(rects), manifold3d.FillRule.Positive)


def _revolve(profile: manifold3d.CrossSection, frame: np.ndarray, tolerance: float) -> manifold3d.Manifold:
    """A profile turned about the axis, as a polygon around each circle (not in it), no more than `tolerance`
    outside it."""
    import manifold3d

    radius = max(profile.bounds()[2], tolerance)
    segments = int(min(1024, max(16, math.ceil(math.pi * math.sqrt(radius / (2 * tolerance))))))
    widened = profile.scale((1 / math.cos(math.pi / segments), 1.0))
    return manifold3d.Manifold.revolve(widened, segments).transform(frame)


def _not_round(solid: manifold3d.Manifold, frame: np.ndarray, tolerance: float) -> manifold3d.Manifold:
    """The solid less its part that is round about the axis, to `tolerance` (a facetted round is round): all a
    turn about the axis moves. Its triangles' profile (`_profile`) holds every circle its surface crosses,
    so each region of the profile plane it leaves out holds circles all inside the solid or all outside;
    the regions inside, turned about the axis, are its round part."""
    import manifold3d

    profile = _profile(solid, frame, tolerance / 10)
    x0, y0, x1, y1 = profile.bounds()
    box = manifold3d.CrossSection.square((x1 + 1.0, y1 - y0 + 2.0)).translate((0.0, y0 - 1.0))
    inside = []
    for region in (box - profile).decompose():
        turned = manifold3d.Manifold.revolve(region, 32).transform(frame)
        if (turned ^ solid).volume() > turned.volume() / 2:
            inside.append(region)
    if not inside:
        return solid
    core = manifold3d.CrossSection.batch_boolean(inside, manifold3d.OpType.Add).offset(
        tolerance, manifold3d.JoinType.Miter
    )
    return solid - _revolve(core, frame, tolerance)


def _distance_to_origin(triangles: np.ndarray) -> np.ndarray:
    """Each 2D triangle's (n × 3 × 2) distance from the origin; 0 for one that covers it."""
    a, ab = triangles, np.roll(triangles, -1, axis=1) - triangles
    t = np.clip(-np.einsum("ijk,ijk->ij", a, ab) / np.maximum(np.einsum("ijk,ijk->ij", ab, ab), 1e-30), 0.0, 1.0)
    edges = np.linalg.norm(a + t[..., None] * ab, axis=-1).min(axis=1)
    turn = ab[..., 0] * -a[..., 1] - ab[..., 1] * -a[..., 0]  # which side of each edge the origin is on
    covers = np.all(turn > 0, axis=1) | np.all(turn < 0, axis=1)  # strictly: one seen edge-on covers nothing
    return np.where(covers, 0.0, edges)


class _Clusters:
    """Union-find over leaf instances: the rigid clusters, and which side of a mate moves."""

    def __init__(self, leaves: dict[str, Leaf]) -> None:
        self.parent = {p: p for p in leaves}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)

    def union_all(self, group: list[str]) -> None:
        for other in group[1:]:
            self.union(group[0], other)

    def side(
        self, start: str, edges: list[Link], *, exclude: Link, ground: str, also: Link | None = None
    ) -> set[str] | None:
        """The clusters reachable from `start` without crossing `exclude` (or `also`); None when ground is among them."""
        graph: dict[str, set[str]] = {}
        for k in edges:
            if k is exclude or k is also:
                continue
            a, b = self.find(k.a.instance), self.find(k.b.instance)
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
        first = self.find(start)
        seen, todo = {first}, deque([first])
        while todo:
            for n in graph.get(todo.popleft(), ()):
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        return None if ground in seen else seen
