"""What moves when an input moves: the world of rigid clusters, which mates' motions are inputs, and each input's
plan, the stages it places the moving clusters by, in order.

Rigid mates, fastener sets and rigid components join instances into rigid clusters; the other mates and the
relations join the clusters. Every free motion of a moving mate is an input, held, or driven (`inputs`): where it
sits as assembled, a motion the ones before it (in order: those with a range, a follower's last, then the rest) do
not already determine is an input when it has a range and is held where it sits when it has none; one they do
determine is driven, by the input that moves it most. An input's plan holds the other inputs, and the held motions,
where they sit, and places what its motion moves stage by stage (`stages`): about or along a fixed axis by a law of
the input (the input's own side, a gear its relation drives), carried by a cluster an earlier stage places, or
settled in a block, given the poses of what moves before it (a four-bar's coupler and rocker given its crank, each
valve train given its lifter).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

import numpy as np

from hitlcad.analysis.base import label, result
from hitlcad.analysis.coverage import Link, nested_links
from hitlcad.analysis.interference import Allowance, allowed
from hitlcad.analysis.model import BuiltDesign, Leaf
from hitlcad.core import frames as fr
from hitlcad.core.laws import Law, Linear, compose
from hitlcad.core.loops import Block, BlockMate, BlockRelation, rank
from hitlcad.core.report import Result
from hitlcad.core.structure import Axis, Mate, Relation, Tolerances, symmetry_of
from hitlcad.kernel.errors import MOTION_HELD, StructureError

if TYPE_CHECKING:
    from hitlcad.analysis.motion.path import Traced
    from hitlcad.analysis.motion.sweep import Swept

NAME = "motion"
_STEP = 0.01  # ° or mm of one end of a relation, to read how its coordinates move by
_HOLDS = 1e-7  # of a step: a relation's coordinate that moves less than this does not move
_FREE = 1e-6  # of the largest: a motion's rate across what the mates leave free below this is none


@dataclass(frozen=True)
class FreeMotion:
    """One of a mate's free motions: its "angle" or "travel"."""

    link: Link
    kind: str

    def __hash__(self) -> int:
        return hash((id(self.link), self.kind))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FreeMotion) and other.link is self.link and other.kind == self.kind


@dataclass(frozen=True)
class Drive:
    """How a cluster moves as the input moves: about or along `axis`, by `law` of the input's motion from where it
    sits."""

    axis: Axis
    law: Law

    def at(self, value: float) -> fr.Frame:
        return self.axis.motion(self.law.at(value))

    def speed(self, radius: float, lo: float, hi: float) -> float:
        """The furthest a point `radius` from the axis travels per unit of the input, within [lo, hi] (mm)."""
        return self.law.steepest(lo, hi) * (1.0 if self.axis.kind == "travel" else math.radians(1.0) * radius)

    def about(self) -> tuple[object, ...]:
        """Its axis, as plain values: what a solid shaped against it is kept by."""
        return (self.axis.kind, *np.round(self.axis.origin, 6).tolist(), *np.round(self.axis.direction, 6).tolist())

    def key(self) -> list[object]:
        return [*self.about(), self.law.key()]


@dataclass
class Stage:
    """How a stage places its clusters: about or along an axis by a law of the input (`drive`), carried where they
    sit on a cluster an earlier stage places (`host`), or settled in a `block`."""

    clusters: list[str]
    drive: Drive | None = None
    host: str | None = None
    block: Block | None = None


@dataclass
class Plan:
    """One input's sweep: its mate's free motion and its range, where it sits, the stages that place what it moves,
    the motions it drives (which its sweep checks), and the world it moves in."""

    input: FreeMotion
    start: float
    end: float
    continuous: bool
    rest: float
    stages: list[Stage]
    driven: list[FreeMotion]
    world: World
    swept: Swept | None = None  # once `sweep` has run
    traced: Traced | None = None  # once `path.positions` has run

    @property
    def link(self) -> Link:
        return self.input.link

    @property
    def kind(self) -> str:
        return self.input.kind

    @property
    def exact(self) -> bool:
        """Every stage about a fixed axis by a law: the sweep is exact, not sampled."""
        return all(s.block is None for s in self.stages)

    @cached_property
    def moving(self) -> list[str]:
        return [c for s in self.stages for c in s.clusters]

    def ends(self) -> list[float]:
        """Where its sweep goes from where it sits: both ways round for a full turn."""
        if self.continuous:
            return [self.rest + 180.0, self.rest - 180.0]
        return [*([self.end] if self.end > self.rest else []), *([self.start] if self.start < self.rest else [])]


@dataclass
class World:
    leaves: dict[str, Leaf]
    clusters: Clusters
    ground: str
    moving_links: list[Link]
    relations: list[Link]
    laws: dict[int, Law] = field(metadata={"doc": "Each relation's law, by its link's id"})
    touching: dict[frozenset[str], Link] = field(metadata={"doc": "Leaf pairs a relation keeps in contact, and it"})
    ok_pairs: dict[frozenset[str], Allowance]
    tolerances: Tolerances
    tracked: dict[frozenset[str], float] = field(
        metadata={"doc": "Leaf pairs a clearance requirement names, and the least gap it needs (0: no minimum)"}
    )

    def leaves_of(self, clusters: set[str]) -> list[str]:
        return [p for p in self.leaves if self.clusters.find(p) in clusters]

    @cached_property
    def corners(self) -> dict[str, np.ndarray]:
        """Each cluster's leaves' box corners, as assembled."""
        out: dict[str, list[np.ndarray]] = {}
        for p, leaf in self.leaves.items():
            out.setdefault(self.clusters.find(p), []).append(corners(leaf.bounds))
        return {c: np.vstack(v) for c, v in out.items()}


class Clusters:
    """Union-find over leaf instances: the rigid clusters."""

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


def ranges(mate: Mate) -> list[tuple[str, float, float, bool]]:
    """A mate's ranges of motion: (free motion, start, end, continuous), each its `limits`, or a full turn for a
    turn without them."""
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
        apart = req.check.keeps_apart() if req.check else None
        if apart is None:
            continue
        between, gap = apart
        try:
            a, b = (built.leaves_of(name) for name in between)
            need = float(built.nominal(gap) if isinstance(gap, str) else gap)
        except StructureError:  # the requirement reports it
            continue
        for la in a:
            for lb in b:
                key = frozenset((la.path, lb.path))
                out[key] = max(out.get(key, 0.0), need)
    return out


def plans(built: BuiltDesign) -> list[Plan | Result]:
    """A plan for every input, and the result saying why a mate with a range does not move on its own."""
    leaves = {leaf.path: leaf for leaf in built.leaves()}
    if not leaves or built.spec.ground is None:
        return []
    all_links, rigid_groups = nested_links(built, lambda design, name: design.spec.components[name].flexible)
    clusters = Clusters(leaves)
    for group in rigid_groups:
        clusters.union_all(group)
    for link in all_links:
        if link.rigid:
            clusters.union(link.a.instance, link.b.instance)
    try:
        ground = clusters.find(built.leaves_of(built.spec.ground)[0].path)
    except (StructureError, IndexError):  # no ground placed, or nothing in it: the structure analysis says so
        return []
    relations = [k for k in all_links if isinstance(k.source, Relation)]
    laws = {id(k): k.source.law(k.ends()) for k in relations}
    touching = {frozenset((k.a.instance, k.b.instance)): k for k in relations if k.source.touching}
    out: list[Plan | Result] = []
    moving_links = []
    for k in all_links:
        if not isinstance(k.source, Mate) or k.rigid:
            continue
        if clusters.find(k.a.instance) != clusters.find(k.b.instance):
            moving_links.append(k)
        elif ranges(k.source):
            out.append(_held(k, "held by rigid mates or fasteners as well"))
    world = World(leaves, clusters, ground, moving_links, relations, laws, touching, allowed(all_links),
                  built.tolerances, _tracked(built))  # fmt: skip
    inputs, held, driven, still = _inputs(world)
    out += [_held(m.link, "held by the rest of its mechanism") for m in still]
    for m in inputs:
        _, lo, hi, continuous = next(r for r in ranges(m.link.source) if r[0] == m.kind)
        rest = _free(m.link, built.tolerances)[m.kind]
        stages = _stages(world, m, held | {i for i in inputs if i != m})
        out.append(Plan(m, lo, hi, continuous, rest, stages, [d for d, by in driven.items() if by == m], world))
    return out


def _held(link: Link, why: str) -> Result:
    mate: Mate = link.source
    return result(NAME, MOTION_HELD, mate.address, True, f"{label(link.a)} ↔ {label(link.b)}: {why}, so it does not "
                  "move", [link.a.instance, link.b.instance, mate.a, mate.b], severity="info")  # fmt: skip


def _free(link: Link, tol: Tolerances) -> dict[str, float]:
    """Where a mate's free motions sit, as assembled."""
    mate: Mate = link.source
    return mate.verify(link.a.frame, mate.facing(link.b.frame), "axial", tol).free


# --------------------------------------------------------------------------- inputs


def _inputs(world: World) -> tuple[list[FreeMotion], set[FreeMotion], dict[FreeMotion, FreeMotion], list[FreeMotion]]:
    """The inputs; the motions held where they sit; each driven motion with a range, and the input that moves it
    most; and the motions with a range that nothing can move (`plans`)."""
    motions = [FreeMotion(k, kind) for k in world.moving_links for kind in k.source.free_motion]
    if not motions:
        return [], set(), {}, []
    block = mechanism(world, generic=True)
    jac, rates = block.jacobians()
    _, s, vt = np.linalg.svd(jac)
    free = vt[int((s > 1e-7 * max(float(s[0]), 1.0)).sum()) :].T  # what the mates and relations leave free
    index = {id(k): i for i, k in enumerate(world.moving_links)}
    row = {m: rates[2 * index[id(m.link)] + (0 if m.kind == "angle" else 1)] @ free for m in motions}
    largest = max((float(np.abs(r).max()) for r in row.values() if r.size), default=0.0)
    ranged = {m for m in motions if any(r[0] == m.kind for r in ranges(m.link.source))}
    followers = {world.clusters.find(r.b.instance) for r in world.relations if world.laws[id(r)].one_way}

    def order(m: FreeMotion) -> tuple[bool, bool]:
        ends = {world.clusters.find(m.link.a.instance), world.clusters.find(m.link.b.instance)}
        return m not in ranged, bool(ends & followers)

    inputs: list[FreeMotion] = []
    held: set[FreeMotion] = set()
    chosen: list[tuple[FreeMotion, np.ndarray]] = []  # the inputs' and the held motions' rows, independent
    driven: dict[FreeMotion, FreeMotion] = {}
    still: list[FreeMotion] = []
    for m in sorted(motions, key=order):
        r = row[m]
        if not r.size or float(np.abs(r).max()) <= _FREE * max(largest, 1.0):  # the mates hold it as it is
            held.add(m)
            if m in ranged:
                still.append(m)
            continue
        if rank(np.array([*(c for _, c in chosen), r])) > len(chosen):
            chosen.append((m, r))
            if m in ranged:
                inputs.append(m)
            else:
                held.add(m)
            continue
        if m not in ranged:
            continue
        weights = np.linalg.lstsq(np.array([c for _, c in chosen]).T, r, rcond=None)[0]
        moved = [(abs(w), c) for w, (c, _) in zip(weights, chosen, strict=True) if c in inputs and abs(w) > _FREE]
        if moved:
            driven[m] = max(moved, key=lambda t: t[0])[1]
        else:
            still.append(m)
    return inputs, held, driven, still


def mechanism(world: World, clusters: list[str] | None = None, given: list[str] | None = None,
              driver: FreeMotion | None = None, generic: bool = False) -> Block:  # fmt: skip
    """A block of `clusters` (every moving one when None) with the mates and relations that touch them, `given` the
    poses of those, and the input's mate as its driver where it is one of them. `generic`: each relation's law a
    straight line at its steepest slope, so what the block couples does not hang on where it sits (a cam on its
    base circle, which as assembled does not lift its follower, still drives it)."""
    find, tol = world.clusters.find, world.tolerances
    if clusters is None:
        clusters = sorted({find(e.instance) for k in [*world.moving_links, *world.relations] for e in (k.a, k.b)}
                          - {world.ground})  # fmt: skip
    inside = set(clusters)
    links = [k for k in world.moving_links if {find(k.a.instance), find(k.b.instance)} & inside]
    mates = []
    for k in links:
        mate: Mate = k.source
        holds = set(mate.holds)
        if symmetry_of(k.a.spec.symmetry, k.b.spec.symmetry) == "axial":
            holds.discard("clocking")
        mates.append(BlockMate(link_label(k), mate, find(k.a.instance), find(k.b.instance), k.a.frame,
                               mate.facing(k.b.frame), frozenset(holds)))  # fmt: skip
    relations = []
    for k in world.relations:
        if not {find(k.a.instance), find(k.b.instance)} & inside:
            continue
        rel: Relation = k.source
        axes = rel.axes(k.a.frame, k.b.frame)
        relations.append(BlockRelation(link_label(k), rel, find(k.a.instance), find(k.b.instance), k.a.frame,
                                       k.b.frame, _generic(world.laws[id(k)]) if generic else world.laws[id(k)],
                                       (axes[0].kind, axes[1].kind),
                                       rel.tolerance or tol.position_mm))  # fmt: skip
    at = next((i for i, k in enumerate(links) if driver is not None and k is driver.link), None)
    return Block(clusters, given or [], mates, relations, at, driver.kind if driver else "angle",
                 {c: world.corners[c] for c in clusters}, tol)  # fmt: skip


# --------------------------------------------------------------------------- stages

_STAYS = "stays"
_ALONG = {"angle": "clocking", "travel": "origin_z"}  # the part of b's frame a free motion moves


def _stages(world: World, input: FreeMotion, held: set[FreeMotion]) -> list[Stage]:
    """The stages that place what `input` moves, with the motions `held` where they sit: about or along a fixed axis
    where they can be, carried where they sit, else settled in blocks given what moves before them, those that can
    be settled at once in one stage. A relation between two clusters placed by laws that its own law does not hold leaves the later one to a
    block (a closed gear train)."""
    forced: set[str] = set()
    while True:
        stages, placed = _place(world, input, held, forced)
        wrong = [r for r in world.relations if _broken(world, r, placed)]
        if not wrong:
            return stages
        forced |= {world.clusters.find(r.b.instance) for r in wrong}


def _place(
    world: World, input: FreeMotion, held: set[FreeMotion], forced: set[str]
) -> tuple[list[Stage], dict[str, object]]:
    find = world.clusters.find
    placed: dict[str, object] = {
        world.ground: _STAYS
    }  # a cluster: _STAYS, its Drive, or its Stage (a block's, a host's)
    stages: list[Stage] = []

    def fixed(k: Link) -> bool:
        """Whether the mate places b from a: it holds all of b's frame but its free motions, and those are held, or
        the input (a ball joint, which holds only b's origin, does not)."""
        motions = {_ALONG[kind]: FreeMotion(k, kind) for kind in k.source.free_motion}
        return all(p in k.source.holds or (p in motions and (motions[p] in held or motions[p] == input))
                   for p in ("origin_xy", "origin_z", "axis", "clocking"))  # fmt: skip

    def stage_of(c: str) -> Stage:
        how = placed[c]
        assert isinstance(how, Stage)
        return how

    def carried(c: str, host: str) -> None:
        how = placed[host]
        if how is _STAYS:
            placed[c] = _STAYS
        elif isinstance(how, Drive):
            next(s for s in stages if s.drive is how).clusters.append(c)
            placed[c] = how
        else:
            root = stage_of(host).host or host
            stage = next((s for s in stages if s.host == root), None)
            if stage is None:
                stages.append(stage := Stage([], host=root))
            stage.clusters.append(c)
            placed[c] = stage

    while True:
        progress = False
        for k in world.moving_links:
            ca, cb = find(k.a.instance), find(k.b.instance)
            if (ca in placed) == (cb in placed) or not fixed(k):
                continue
            known, c = (ca, cb) if ca in placed else (cb, ca)
            if c in forced:
                continue
            if FreeMotion(k, input.kind) == input and input.link is k:
                if placed[known] is not _STAYS:
                    continue  # the input on a moving part: settled in a block
                fa = k.a.frame
                drive = Drive(Axis(input.kind, fr.origin(fa), fr.z_axis(fa)), Linear(1.0 if c == cb else -1.0))
                stages.append(Stage([c], drive=drive))
                placed[c] = drive
            else:
                carried(c, known)
            progress = True
        for r in world.relations:
            ca, cb = find(r.a.instance), find(r.b.instance)
            if (ca in placed) == (cb in placed):
                continue
            from_a = ca in placed
            known, c = (ca, cb) if from_a else (cb, ca)
            if c in forced or not isinstance(placed[known], Drive) or (not from_a and world.laws[id(r)].one_way):
                continue
            axis = r.source.axes(r.a.frame, r.b.frame)[1 if from_a else 0]
            anchors = [k for k in world.moving_links if c in (find(k.a.instance), find(k.b.instance))
                       and _other(k, c, find) in placed]  # fmt: skip
            if not anchors or not all(placed[_other(k, c, find)] is _STAYS and _allows(world, k, axis, held)
                                      for k in anchors):  # fmt: skip
                continue
            drive = _carried(world, r, {known: placed[known]}, from_a)  # type: ignore[dict-item]
            if drive is None:
                continue
            stages.append(Stage([c], drive=drive))
            placed[c] = drive
            progress = True
        if progress:
            continue
        blocks = _blocks(world, placed, input)
        if not blocks:
            return stages, placed
        members = sorted(c for group, _ in blocks for c in group)  # independent of each other: settled in one call
        given = sorted({g for _, gs in blocks for g in gs})
        stage = Stage(members, block=mechanism(world, members, given, input))
        stages.append(stage)
        placed.update(dict.fromkeys(members, stage))


def _allows(world: World, k: Link, axis: Axis, held: set[FreeMotion]) -> bool:
    """Whether the mate lets its moving end move about or along `axis`, and only so: its free motion of that kind on
    that axis, its others held (a shaft in two coaxial bearings, a tappet in its bore)."""
    tol, z, o = world.tolerances, fr.z_axis(k.a.frame), fr.origin(k.a.frame)
    if axis.kind not in k.source.free_motion or fr.angle_deg(z, axis.direction) > tol.angle_deg and \
            fr.angle_deg(z, -axis.direction) > tol.angle_deg:  # fmt: skip
        return False
    if axis.kind == "angle" and fr.distance_to_line(axis.origin, o, z) > tol.position_mm:
        return False
    return all(FreeMotion(k, kind) in held for kind in k.source.free_motion if kind != axis.kind)


def _generic(law: Law) -> Law:
    """A law as a straight line at its steepest slope over a turn: what it couples, wherever it sits."""
    return law if isinstance(law, Linear) else Linear(law.steepest(-180.0, 180.0))


def _other(k: Link, c: str, find: Callable[[str], str]) -> str:
    """The cluster at the mate's other end from `c`."""
    return find(k.a.instance) if find(k.b.instance) == c else find(k.b.instance)


def _blocks(world: World, placed: dict[str, object], input: FreeMotion) -> list[tuple[list[str], list[str]]]:
    """The clusters left to place, joined by their mates and relations, each group next to a moving placed cluster
    or holding the input's mate: each group and the moving placed clusters it is given."""
    find = world.clusters.find
    near: dict[str, set[str]] = {}
    for k in [*world.moving_links, *world.relations]:
        a, b = find(k.a.instance), find(k.b.instance)
        near.setdefault(a, set()).add(b)
        near.setdefault(b, set()).add(a)
    driving = {find(input.link.a.instance), find(input.link.b.instance)}
    out, seen = [], set()
    for start in sorted(near):
        if start in placed or start in seen:
            continue
        group, todo = {start}, deque([start])
        while todo:
            for n in near[todo.popleft()]:
                if n not in placed and n not in group:
                    group.add(n)
                    todo.append(n)
        seen |= group
        given = sorted({n for c in group for n in near[c] if n in placed and placed[n] is not _STAYS})
        if given or group & driving:
            out.append((sorted(group), given))
    return out


def _broken(world: World, rel: Link, placed: dict[str, object]) -> bool:
    """Whether two clusters placed by laws (or staying) move so that the relation between them does not hold."""
    find = world.clusters.find
    ends = [placed.get(find(e.instance)) for e in (rel.a, rel.b)]
    if not all(e is _STAYS or isinstance(e, Drive) for e in ends) or all(e is _STAYS for e in ends):
        return False
    drives = {find(e.instance): how for e, how in zip((rel.a, rel.b), ends, strict=True) if isinstance(how, Drive)}
    qa, qb = _coordinates(rel, {c: d.at(1.0) for c, d in drives.items()}, find)
    return abs(qb - world.laws[id(rel)].at(qa)) > 1e-9


def link_label(link: Link) -> str:
    """A link by its frames: 'base.hinge+lid.pin', or 'sun.teeth~planet[1].teeth' for a relation."""
    return f"{label(link.a)}{'~' if isinstance(link.source, Relation) else '+'}{label(link.b)}"


def _carried(world: World, rel: Link, drives: dict[str, Drive], from_a: bool) -> Drive | None:
    """The drive of the relation's unplaced end, about or along its own axis, that holds the relation as the other
    end moves by its drive; None where none can. Moved along fixed axes the relation's coordinates are linear in
    the ends' (each read from a small step of one end): a straight-line law holds at the rate that keeps it, and
    any law where each coordinate is its own end's alone."""
    find, law = world.clusters.find, world.laws[id(rel)]
    driver, other = (rel.a, rel.b) if from_a else (rel.b, rel.a)
    given = drives[find(driver.instance)]
    axis = rel.source.axes(rel.a.frame, rel.b.frame)[1 if from_a else 0]
    (qa1, qb1), (qa2, qb2) = (_coordinates(rel, {find(end.instance): along.motion(_STEP)}, find)
                              for end, along in ((driver, given.axis), (other, axis)))  # fmt: skip
    if isinstance(law, Linear):  # off = qb − r·qa, linear in both ends' steps
        c_driver, c_other = qb1 - law.ratio * qa1, qb2 - law.ratio * qa2
        if abs(c_other) < _HOLDS * _STEP:
            return None
        return Drive(axis, compose(Linear(-c_driver / c_other), given.law))
    if max(abs(qa2), abs(qb1)) > _HOLDS * _STEP or abs(qb2) < _HOLDS * _STEP:
        return None
    return Drive(axis, compose(law, given.law, qa1 / _STEP, _STEP / qb2))


def _coordinates(rel: Link, moved: dict[str, fr.Frame], find: Callable[[str], str]) -> tuple[float, float]:
    """The relation's coordinates once its ends' clusters are `moved`."""
    fa, fb = rel.a.frame, rel.b.frame
    ma, mb = (moved.get(find(end.instance), fr.IDENTITY) @ end.frame for end in (rel.a, rel.b))
    return rel.source.coordinates(fa, fb, ma, mb)


def corners(bounds: np.ndarray) -> np.ndarray:
    return np.array([[x, y, z] for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]])


def unit(kind: str) -> str:
    return "°" if kind == "angle" else " mm"
