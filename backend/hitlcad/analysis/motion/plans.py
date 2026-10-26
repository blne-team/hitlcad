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
from hitlcad.core.report import Cause, Result
from hitlcad.core.structure import Axis, Mate, Relation, Tolerances
from hitlcad.kernel.errors import MOTION_HELD, StructureError

if TYPE_CHECKING:
    from hitlcad.analysis.motion.loop import LoopSwept
    from hitlcad.analysis.motion.tree import Swept

NAME = "motion"
_UNIT = 0.01  # of the driving motion, to read a driven rate by


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
    world = World(leaves, clusters, ground, moving_links, relations, allowed(all_links), built.tolerances,
                  _tracked(built))  # fmt: skip
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
    out += [p for p in trees if id(p.link) not in in_loops]
    out += [_loop_plan([edges[n] for n in sorted(blocks[i])], edges, world) for i in sorted(solve)]
    return out


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
    """Each moving cluster's drive per unit of the mate's `kind`, carried on through relations along the
    axis each gives the cluster it drives, at the rate that holds the relation (`Relation.coordinates`); and
    the relations no drive about a fixed axis can hold: one coupling a moving cluster to one that cannot
    move, or two driven ones at rates it does not hold (a closed train)."""
    w, fa = plan.world, plan.link.a.frame
    drives = dict.fromkeys(plan.moving, Drive(Axis(kind, fr.origin(fa), fr.z_axis(fa)), plan.sign))  # `Mate.motion`
    stuck: dict[int, Link] = {}  # by id: links compare by their frames
    changed = True
    while changed:
        changed = False
        for rel in w.relations:
            ca, cb = w.clusters.find(rel.a.instance), w.clusters.find(rel.b.instance)
            if (ca in drives) == (cb in drives) or id(rel) in stuck:
                continue
            axis_a, axis_b = rel.source.axes(rel.a.frame, rel.b.frame)
            driven, axis = (rel.b, axis_b) if ca in drives else (rel.a, axis_a)
            off = [
                _off(rel, drives | {w.clusters.find(driven.instance): Drive(axis, rate)}, w.clusters.find)
                for rate in (0.0, 1.0)
            ]
            if math.isclose(off[0], off[1], abs_tol=1e-12):  # its own motion does not move the relation
                stuck[id(rel)] = rel
                continue
            drive = Drive(axis, off[0] / (off[0] - off[1]))
            own = [k for k in w.moving_links if k is not plan.link and w.clusters.find(driven.instance) in
                   (w.clusters.find(k.a.instance), w.clusters.find(k.b.instance))]  # fmt: skip
            for k in own:
                side = w.clusters.side(driven.instance, w.moving_links, exclude=k, ground=w.ground, also=plan.link)
                if side is None or side & drives.keys():
                    continue
                drives.update(dict.fromkeys(side, drive))
                changed = True
                break
    stuck |= {id(r): r for r in w.relations if id(r) not in stuck and (
        (w.clusters.find(r.a.instance) in drives) != (w.clusters.find(r.b.instance) in drives)
        or w.clusters.find(r.a.instance) in drives and abs(_off(r, drives, w.clusters.find)) > 1e-9)}  # fmt: skip
    return drives, list(stuck.values())


def _off(rel: Link, drives: dict[str, Drive], cluster: Callable[[str], str]) -> float:
    """How far a relation is from holding (b's units) after `_UNIT` of the drives moves its ends' clusters."""
    spec: Relation = rel.source
    fa, fb = rel.a.frame, rel.b.frame
    moved = [
        d.at(_UNIT) @ end.frame if (d := drives.get(cluster(end.instance))) else end.frame for end in (rel.a, rel.b)
    ]
    qa, qb = spec.coordinates(fa, fb, *moved)
    return qb - spec.ratio(rel.a.owner.nominals(rel.a.spec), rel.b.owner.nominals(rel.b.spec)) * qa


def unit(kind: str) -> str:
    return "°" if kind == "angle" else " mm"
