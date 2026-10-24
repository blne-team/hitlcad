"""Motion: every mate with a range of motion (its `limits`, or a full turn for a revolute without limits)
is moved through it, and nothing may collide on the way.

The parts that move are the side of the mate away from `ground`: rigid mates, fastener sets and rigid
components join instances into rigid clusters, and the other mates join the clusters. Relations carry
the motion on: a gear driven by a moving gear turns about its own axis by the relation's ratio. A mate
inside a closed loop of mates moves only together with the rest of the loop, which takes a solver;
hitlcad checks rather than solves, so it reports such a mate's motion as unverified.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from hitlcad.analysis.base import Context, label, result
from hitlcad.analysis.coverage import Link, nested_links
from hitlcad.analysis.interference import allowed, boxes_touch, overlap, placed_bounds
from hitlcad.analysis.model import Leaf
from hitlcad.core import frames as fr
from hitlcad.core.report import Result
from hitlcad.core.structure import _Mate, _Relation


class Motion:
    name = "motion"

    def run(self, ctx: Context) -> list[Result]:
        built = ctx.built
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
        relations = [k for k in all_links if isinstance(k.source, _Relation)]
        ok_pairs = allowed(all_links)
        out = []
        for link in moving_links:
            mate: _Mate = link.source
            ranges = self._ranges(mate, link)
            if not ranges:
                continue
            out.append(self._sweep(ctx, link, ranges, moving_links, relations, clusters, ground, leaves, ok_pairs))
        return out

    @staticmethod
    def _ranges(mate: _Mate, link: Link) -> list[tuple[str, float, float, bool]]:
        """(free motion, start, end, continuous) to sweep."""
        limits = mate.limits_of()
        out = []
        for kind in mate.free_motion:
            bounds = getattr(limits, kind) if limits else None
            if bounds:
                out.append((kind, bounds[0], bounds[1], False))
            elif kind == "angle":
                out.append((kind, 0.0, 360.0, True))
        return out

    def _sweep(self, ctx: Context, link: Link, ranges: list[tuple[str, float, float, bool]], moving_links: list[Link],
               relations: list[Link], clusters: _Clusters, ground: str, leaves: dict[str, Leaf],
               ok_pairs: dict[frozenset[str], str]) -> Result:  # fmt: skip
        mate: _Mate = link.source
        about = [link.a.instance, link.b.instance, mate.a, mate.b]
        if clusters.find(link.a.instance) == clusters.find(link.b.instance):
            return result(self.name, mate.address, True, f"{label(link.a)} ↔ {label(link.b)}: held by rigid mates or "
                          "fasteners as well, so it does not move", about, severity="info")  # fmt: skip
        side_b = clusters.side(link.b.instance, moving_links, exclude=link, ground=ground)
        side_a = clusters.side(link.a.instance, moving_links, exclude=link, ground=ground)
        if side_b is None and side_a is None:
            return result(self.name, mate.address, None, f"{label(link.a)} ↔ {label(link.b)}: in a closed loop of "
                          "mates, it moves only with the rest of the loop; not analysed without a solver", about)  # fmt: skip
        moving, sign = (side_b, 1.0) if side_b is not None else (side_a, -1.0)
        assert moving is not None
        free = mate.verify(link.a.frame, mate.facing(link.b.frame), "axial", ctx.tol).free
        n = ctx.tol.motion_samples
        swept = []
        for kind, lo, hi, continuous in ranges:
            current = free.get(kind, 0.0)
            values = (current + np.arange(n) * (hi - lo) / n) if continuous else np.linspace(lo, hi, n)
            values = sorted(values, key=lambda v: abs(v - current))  # the first collision reached from where it sits
            unit = "°" if kind == "angle" else " mm"
            for value in values:
                delta = float(value - current)
                motions = {c: mate.motion(link.a.frame, kind, sign * delta) for c in moving}
                stuck = self._propagate(motions, kind, delta, link, moving_links, relations, clusters, ground)
                if stuck:
                    return result(self.name, mate.address, None, f"{label(link.a)} ↔ {label(link.b)}: its motion is "
                                  f"coupled by {', '.join(sorted(stuck))} to parts that cannot move with it here (a "
                                  "closed loop or the ground); not analysed without a solver", about)  # fmt: skip
                hit = self._collision(ctx, motions, clusters, leaves, ok_pairs)
                if hit:
                    a, b, shared = hit
                    return result(self.name, mate.address, False, f"{label(link.a)} ↔ {label(link.b)} at "
                                  f"{value:.1f}{unit}: {a} hits {b} ({shared:.2f} mm³)", [*about, a, b],
                                  {kind: float(value), "overlap_mm3": shared})  # fmt: skip
            swept.append(f"{'a full turn' if continuous else f'{lo:g} … {hi:g}{unit}'}")
        return result(self.name, mate.address, True, f"{label(link.a)} ↔ {label(link.b)} moves through "
                      f"{', '.join(swept)} ({n} positions) without collision", about)  # fmt: skip

    def _propagate(self, motions: dict[str, fr.Frame], kind: str, delta: float, driver: Link, moving_links: list[Link],
                   relations: list[Link], clusters: _Clusters, ground: str) -> set[str]:  # fmt: skip
        """Carry the motion through relations to the clusters they drive, each about its own mate's axis.
        Returns the relations that couple a moving cluster to one that cannot move."""
        amounts = {c: delta for c in motions}
        changed = True
        while changed:
            changed = False
            for rel in relations:
                ca, cb = clusters.find(rel.a.instance), clusters.find(rel.b.instance)
                if (ca in motions) == (cb in motions):
                    continue
                rel_spec: _Relation = rel.source
                na, nb = rel.a.owner.nominals(rel.a.spec), rel.b.owner.nominals(rel.b.spec)
                ratio = rel_spec.ratio(na, nb)
                drv, driven, amount = (
                    (ca, rel.b, amounts[ca] * ratio) if ca in motions else (cb, rel.a, amounts[cb] / ratio)
                )
                own = [k for k in moving_links if k is not driver and clusters.find(driven.instance) in
                       (clusters.find(k.a.instance), clusters.find(k.b.instance))]  # fmt: skip
                for k in own:
                    side = clusters.side(driven.instance, moving_links, exclude=k, ground=ground, also=driver)
                    if side is None or side & motions.keys():
                        continue
                    mate: _Mate = k.source
                    turn = "angle" if "angle" in mate.free_motion else "travel"
                    on_b = clusters.find(k.b.instance) in side
                    step = mate.motion(k.a.frame, turn, amount if on_b else -amount)
                    for c in side:
                        motions[c] = step
                        amounts[c] = amount
                    changed = True
                    break
        return {
            r.source.address
            for r in relations
            if (clusters.find(r.a.instance) in motions) != (clusters.find(r.b.instance) in motions)
        }

    def _collision(self, ctx: Context, motions: dict[str, fr.Frame], clusters: _Clusters, leaves: dict[str, Leaf],
                   ok_pairs: dict[frozenset[str], str]) -> tuple[str, str, float] | None:  # fmt: skip
        pad = ctx.tol.position_mm
        moved = {p: motions[clusters.find(p)] for p in leaves if clusters.find(p) in motions}
        bounds = {p: placed_bounds(leaf, moved.get(p, fr.IDENTITY)) for p, leaf in leaves.items()}
        for p, motion in moved.items():
            for q, other in leaves.items():
                if q == p or clusters.find(q) == clusters.find(p) or frozenset((p, q)) in ok_pairs:
                    continue
                if q in moved and q < p:
                    continue  # each moved pair once
                if not boxes_touch(bounds[p], bounds[q], pad):
                    continue
                try:
                    shared = overlap(ctx, leaves[p], motion, other, moved.get(q, fr.IDENTITY))
                except ValueError:
                    continue  # an open mesh: the interference analysis already says so
                if shared > ctx.settings.overlap_mm3:
                    return p, q, shared
        return None


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
