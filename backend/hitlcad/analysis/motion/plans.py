"""What moves when a mate moves: the world of rigid clusters, and a plan for each sweep.

Rigid mates, fastener sets and rigid components join instances into rigid clusters; the other mates and the
relations join the clusters. A mate whose motion moves the side of it away from `ground` about a fixed axis (and
gears that side drives about theirs, at the rates their relations hold, `drives`) gets a `Plan`, swept exactly
(`tree`). A block of mates and relations that moves only together gets one `LoopPlan`, swept by a loop solver
(`loop`): a closed loop of mates (a four-bar), or a relation no drive about a fixed axis can hold (a planet on a
moving carrier meshing with a fixed ring).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from hitlcad.analysis.base import label, result
from hitlcad.analysis.coverage import Link, nested_links
from hitlcad.analysis.interference import Allowance, allowed
from hitlcad.analysis.model import BuiltDesign, Leaf
from hitlcad.core import frames as fr
from hitlcad.core.laws import Law, Linear, compose
from hitlcad.core.report import Cause, Result
from hitlcad.core.structure import Axis, Mate, Relation, Tolerances
from hitlcad.kernel.errors import MOTION_HELD, StructureError

if TYPE_CHECKING:
    from hitlcad.analysis.motion.loop import LoopSwept
    from hitlcad.analysis.motion.tree import Swept

NAME = "motion"
_STEP = 0.01  # ° or mm of one end of a relation, to read how its coordinates move by
_HOLDS = 1e-7  # of a step: a relation's coordinate that moves less than this does not move
_UNIT_MM = 0.5  # of an input's travel, as 1° of its turn: the steps a driven mate's motion is followed by
_MOVES = 1e-6  # ° or mm: a driven mate that moves less than this does not move


@dataclass
class Plan:
    """One input's sweep: its free motions and ranges, which side moves, the world it moves in, and the mates whose
    motion it drives (`driven`), which it checks along its own."""

    link: Link
    ranges: list[tuple[str, float, float, bool]]  # (free motion, start, end, continuous)
    moving: set[str]  # clusters
    sign: float
    free: dict[str, float]
    world: World
    driven: list[Driven] = field(default_factory=list)
    swept: Swept | None = None  # once `sweep` has run


@dataclass
class Driven:
    """A mate's free motion an input drives: how far it moves (from, to) as the input moves through its range
    `kind`."""

    link: Link
    kind: str
    by: str  # the input's free motion
    span: tuple[float, float]


@dataclass
class LoopPlan:
    """A loop's sweep: a block of clusters that move only together (a closed loop of mates, or a train a relation
    closes), what rides on them, its mates and relations, and the world it moves in. Each of its mates with a
    range of motion takes its result from this one sweep, driven by one of them (`loop`)."""

    links: list[Link]  # its moving mates, in the order of `nested_links`
    relations: list[Link]
    clusters: list[str]  # what moves, each solved
    reference: str  # what the loop hangs from: ground, or its part nearest ground
    riders: dict[str, str]  # a cluster hanging from a moving one, held where it sits on it
    world: World
    swept: LoopSwept | None = None  # once `loop.sweep` has run

    @property
    def cause(self) -> str:
        return Cause.loop([link_label(k) for k in self.links])

    def leaves_of(self, cluster: str) -> list[str]:
        """The leaves a moving cluster carries: its own and its riders'."""
        return self.world.leaves_of({cluster, *(r for r, host in self.riders.items() if host == cluster)})


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


@dataclass(frozen=True)
class Drive:
    """How a cluster moves as the input moves: about or along `axis`, by `law` of the input's motion."""

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


class Clusters:
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


def plans(built: BuiltDesign) -> list[Plan | LoopPlan | Result]:
    """A plan for every mate with a range of motion, one for each loop such mates are in, or the result saying
    why a mate does not move on its own. A block of mates and relations goes to a loop solver when it closes a
    loop of mates, or a relation in it couples parts no drive about a fixed axis can (`drives`); a gear train
    that is a tree keeps the exact sweep."""
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
    moving_links = [k for k in all_links if isinstance(k.source, Mate) and not k.rigid]
    relations = [k for k in all_links if isinstance(k.source, Relation)]
    laws = {id(k): k.source.law(k.ends()) for k in relations}
    touching = {frozenset((k.a.instance, k.b.instance)): k for k in relations if k.source.touching}
    world = World(leaves, clusters, ground, moving_links, relations, laws, touching, allowed(all_links),
                  built.tolerances, _tracked(built))  # fmt: skip
    edges = [*moving_links, *relations]
    blocks = _blocks(edges, clusters)
    block_of = {id(edges[n]): i for i, block in enumerate(blocks) for n in block}
    loops = {id(moving_links[n]) for block in _blocks(moving_links, clusters) for n in block}  # mates closing loops
    solve = {block_of[id(k)] for k in moving_links if id(k) in loops}
    out: list[Plan | LoopPlan | Result] = []
    trees: list[Plan] = []
    for link in moving_links:
        mate: Mate = link.source
        spans = ranges(mate)
        if not spans:
            continue
        if clusters.find(link.a.instance) == clusters.find(link.b.instance):
            out.append(result(NAME, MOTION_HELD, mate.address, True, f"{label(link.a)} ↔ {label(link.b)}: held by rigid mates or "
                              "fasteners as well, so it does not move", [link.a.instance, link.b.instance, mate.a, mate.b],
                              severity="info"))  # fmt: skip
            continue
        if id(link) in loops:
            continue
        side_b = clusters.side(link.b.instance, moving_links, exclude=link, ground=ground)
        side_a = clusters.side(link.a.instance, moving_links, exclude=link, ground=ground)
        moving, sign = (side_b, 1.0) if side_b is not None else (side_a, -1.0)
        assert moving is not None  # not in a loop of mates: one side is away from ground
        free = mate.verify(link.a.frame, mate.facing(link.b.frame), "axial", built.tolerances).free
        plan = Plan(link, spans, moving, sign, free, world)
        if any(drives(plan, kind)[1] for kind, *_ in spans):
            solve.add(block_of[id(link)])  # the relation it cannot hold closes a loop through this mate
        trees.append(plan)
    in_loops = {id(edges[n]) for i in solve for n in blocks[i]}
    out += _inputs([p for p in trees if id(p.link) not in in_loops])
    out += [_loop_plan([edges[n] for n in sorted(blocks[i])], edges, world) for i in sorted(solve)]
    return out


def _inputs(trees: list[Plan]) -> list[Plan]:
    """The plans of the inputs: each mate's free motion in turn, but one an input before it already moves, which
    that input drives (`Plan.driven`) and its sweep checks. A plan left with no range of its own is no input. A
    follower's own motion comes after the rest, so its cam drives it, not it the cam."""

    def follows(plan: Plan) -> bool:
        find, w = plan.world.clusters.find, plan.world
        return any(w.laws[id(r)].one_way and find(r.b.instance) in plan.moving and find(r.a.instance) not in plan.moving
                   for r in w.relations)  # fmt: skip

    inputs: list[Plan] = []
    for plan in sorted(trees, key=follows):
        own = []
        for span in plan.ranges:
            kind = span[0]
            found = next(((p, by, moved) for p in inputs for by, *_ in p.ranges
                          if (moved := _moved(p, by, plan.link, kind)) is not None), None)  # fmt: skip
            if found is None:
                own.append(span)
                continue
            by_plan, by, moved = found
            rest = plan.free.get(kind, 0.0)
            by_plan.driven.append(Driven(plan.link, kind, by, (rest + min(moved), rest + max(moved))))
        if own:
            plan.ranges = own
            inputs.append(plan)
    return inputs


def _moved(plan: Plan, kind: str, link: Link, free: str) -> list[float] | None:
    """How far `link`'s free motion `free` moves from where it sits, at positions through `plan`'s range `kind`
    (counted on past a half turn); None when that range does not move it."""
    driven = drives(plan, kind)[0]
    find = plan.world.clusters.find
    da, db = driven.get(find(link.a.instance)), driven.get(find(link.b.instance))
    if da is db:
        return None
    fa = link.a.frame
    coordinate = Axis(free, fr.origin(fa), fr.z_axis(fa))  # `Mate.motion`
    rest = plan.free.get(kind, 0.0)
    _, lo, hi, continuous = next(r for r in plan.ranges if r[0] == kind)
    ends = (rest - 180.0, rest + 180.0) if continuous else (lo, hi)
    out = []
    for end in ends:
        n = max(int(abs(end - rest) / (1.0 if kind == "angle" else _UNIT_MM)), 1)
        total = last = 0.0
        for i in range(1, n + 1):
            value = (end - rest) * i / n
            motion = np.linalg.inv(da.at(value) if da else fr.IDENTITY) @ (db.at(value) if db else fr.IDENTITY)
            now = coordinate.of(motion)
            total += fr.reduce_angle(now - last, None) if free == "angle" else now - last
            last = now
            out.append(total)
    return out if max(abs(v) for v in out) > _MOVES else None


def _blocks(edges: list[Link], clusters: Clusters) -> list[list[int]]:
    """The blocks of the graph whose nodes are clusters and links, each link between its two clusters, that close
    a loop: each as its links' indices."""
    import networkx as nx

    graph = nx.Graph()
    for n, k in enumerate(edges):
        graph.add_edges_from([(clusters.find(k.a.instance), n), (n, clusters.find(k.b.instance))])
    return [found for block in nx.biconnected_components(graph)
            if len(found := [n for n in block if isinstance(n, int)]) > 1]  # fmt: skip


def _loop_plan(block: list[Link], edges: list[Link], world: World) -> LoopPlan:
    """A block's loop plan: what it hangs from (ground, or its cluster nearest ground) and what rides on it."""
    find = world.clusters.find
    members = {find(e) for k in block for e in (k.a.instance, k.b.instance)}
    near: dict[str, set[str]] = {}
    for k in edges:
        a, b = find(k.a.instance), find(k.b.instance)
        near.setdefault(a, set()).add(b)
        near.setdefault(b, set()).add(a)
    reference, seen, todo = world.ground, {world.ground}, deque([world.ground])
    while reference not in members:  # a block is connected to ground: where the path from it enters the block
        for n in sorted(near.get(todo.popleft(), ())):
            if n not in seen:
                seen.add(n)
                todo.append(n)
                if n in members:
                    reference = n
                    break
    riders: dict[str, str] = {}
    for host in sorted(members - {reference}):
        stack = [n for n in near.get(host, ()) if n not in members]
        while stack:
            n = stack.pop()
            if n not in riders and n not in members:
                riders[n] = host
                stack += near.get(n, ())
    return LoopPlan([k for k in block if isinstance(k.source, Mate)], [k for k in block if isinstance(k.source, Relation)],
                    sorted(members - {reference}), reference, riders, world)  # fmt: skip


def link_label(link: Link) -> str:
    """A link by its frames: 'base.hinge+lid.pin', or 'sun.teeth~planet[1].teeth' for a relation."""
    return f"{label(link.a)}{'~' if isinstance(link.source, Relation) else '+'}{label(link.b)}"


def drives(plan: Plan, kind: str) -> tuple[dict[str, Drive], list[Link]]:
    """Each moving cluster's drive by the mate's `kind`, carried on through relations along the axis each gives
    the cluster it drives, by the law that holds the relation (`_carried`); and the relations no drive about a
    fixed axis can hold: one coupling a moving cluster to one that cannot move, or two driven ones by laws it
    does not hold (a closed train). A follower does not drive its cam back: a one-way law driven from b holds
    nothing still."""
    w, fa = plan.world, plan.link.a.frame
    find = w.clusters.find
    drives = dict.fromkeys(
        plan.moving, Drive(Axis(kind, fr.origin(fa), fr.z_axis(fa)), Linear(plan.sign))
    )  # `Mate.motion`
    stuck: dict[int, Link] = {}  # by id: links compare by their frames
    changed = True
    while changed:
        changed = False
        for rel in w.relations:
            ca, cb = find(rel.a.instance), find(rel.b.instance)
            if (ca in drives) == (cb in drives) or id(rel) in stuck or _back(w, rel, cb in drives):
                continue
            drive = _carried(w, rel, drives, ca in drives)
            if drive is None:
                stuck[id(rel)] = rel
                continue
            driven = rel.b if ca in drives else rel.a
            own = [k for k in w.moving_links if k is not plan.link and find(driven.instance) in
                   (find(k.a.instance), find(k.b.instance))]  # fmt: skip
            for k in own:
                side = w.clusters.side(driven.instance, w.moving_links, exclude=k, ground=w.ground, also=plan.link)
                if side is None or side & drives.keys():
                    continue
                drives.update(dict.fromkeys(side, drive))
                changed = True
                break
    stuck |= {id(r): r for r in w.relations if id(r) not in stuck and (
        (find(r.a.instance) in drives) != (find(r.b.instance) in drives) and not _back(w, r, find(r.b.instance) in drives)
        or find(r.a.instance) in drives and find(r.b.instance) in drives and abs(_off(w, r, drives)) > _HOLDS)}  # fmt: skip
    return drives, list(stuck.values())


def _back(world: World, rel: Link, from_b: bool) -> bool:
    """Whether the relation is driven from b by a law that only drives b from a."""
    return from_b and world.laws[id(rel)].one_way


def _carried(world: World, rel: Link, drives: dict[str, Drive], from_a: bool) -> Drive | None:
    """The drive of the relation's undriven end, about or along its own axis, that holds the relation as the other
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


def _off(world: World, rel: Link, drives: dict[str, Drive]) -> float:
    """How far a relation is from holding (b's units) a unit of the input on."""
    find = world.clusters.find
    qa, qb = _coordinates(rel, {c: d.at(1.0) for c, d in drives.items()}, find)
    return qb - world.laws[id(rel)].at(qa)


def unit(kind: str) -> str:
    return "°" if kind == "angle" else " mm"
