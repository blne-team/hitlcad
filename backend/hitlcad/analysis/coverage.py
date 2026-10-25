"""Coverage: mates, relations and fastener sets connect every instance to `ground`. An instance nothing
connects is floating: placed, but related to nothing (the real-CAD "under-defined component")."""

from __future__ import annotations

import dataclasses
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hitlcad.analysis.base import Context, pair, result, structural
from hitlcad.analysis.fasteners import stacks
from hitlcad.analysis.mates import agree
from hitlcad.analysis.model import BuiltDesign, PlacedFrame, StructureError
from hitlcad.analysis.relations import relation_pairs
from hitlcad.core import frames as fr
from hitlcad.core.address import InstancePath
from hitlcad.core.report import Cause, Result
from hitlcad.core.structure import _Mate


@dataclass
class Link:
    """Two instances joined by a mate (`kind` its type), a relation, or a fastener set ("fastener")."""

    a: PlacedFrame
    b: PlacedFrame
    kind: str
    source: Any
    role: str = field(default="", metadata={"doc": "In a fastener set: 'through' or 'into'"})

    @property
    def rigid(self) -> bool:
        return self.kind in ("rigid", "fastener")


def links(built: BuiltDesign) -> tuple[list[Link], list[Result]]:
    """Every link of a design's mates, relations and fastener sets, and structure errors met on the way."""
    out, errors = [], []
    for mate in built.spec.mates:
        try:
            out += [Link(fa, fb, mate.type, mate) for fa, fb in pair(built.frames(mate.a), built.frames(mate.b))]
        except StructureError as e:
            errors.append(structural(mate.address, e, [mate.a, mate.b]))
    for rel in built.spec.relations:
        try:
            out += [Link(fa, fb, rel.type, rel) for fa, fb in relation_pairs(built, rel)]
        except StructureError as e:
            errors.append(structural(rel.address, e, [rel.a, rel.b]))
    for fs in built.spec.fasteners:
        try:
            paired = stacks(built, fs)
        except StructureError as e:
            errors.append(structural(fs.address, e, [fs.fastener, *fs.through, fs.into]))
            continue
        for seat, stack in paired:
            out += [Link(seat, f, "fastener", fs, "through") for f in stack[:-1]]
            out.append(Link(seat, stack[-1], "fastener", fs, "into"))
    return out, errors


def nested_links(built: BuiltDesign, expand: Callable[[BuiltDesign, str], bool], prefix: str = "",
                 transform: fr.Frame = fr.IDENTITY) -> tuple[list[Link], list[list[str]]]:  # fmt: skip
    """Links of a design and of the components `expand` opens, in the design's frame, and the leaf paths of
    each component it keeps closed (one rigid group each)."""
    found = [_scoped(k, prefix, transform) for k in links(built)[0]]
    groups = []
    for address, name, local in built.instances():
        if name not in built.components:
            continue
        child, placed = built.components[name], transform @ local
        if expand(built, name):
            inner, inner_groups = nested_links(child, expand, f"{prefix}{address}/", placed)
            found += inner
            groups += inner_groups
        else:
            groups.append([leaf.path for leaf in child.leaves(f"{prefix}{address}/", placed)])
    return found, groups


def _scoped(link: Link, prefix: str, transform: fr.Frame) -> Link:
    if not prefix:
        return link

    def move(f: PlacedFrame) -> PlacedFrame:
        return dataclasses.replace(f, instance=f"{prefix}{f.instance}", frame=transform @ f.frame)

    return dataclasses.replace(link, a=move(link.a), b=move(link.b))


def top(instance: str) -> str:
    return str(InstancePath.parse(instance).head)


def hops(built: BuiltDesign, found: list[Link]) -> dict[str, int]:
    """How many links each top-level instance is from `ground`; one nothing connects to it is left out."""
    if built.spec.ground is None:
        return {}
    grounded = [a for a, _ in built.select(InstancePath.parse(built.spec.ground).head)]
    neighbours: defaultdict[str, set[str]] = defaultdict(set)
    for link in found:
        a, b = top(link.a.instance), top(link.b.instance)
        neighbours[a].add(b)
        neighbours[b].add(a)
    out, todo = dict.fromkeys(grounded, 0), deque(grounded)
    while todo:
        at = todo.popleft()
        for n in neighbours[at] - out.keys():
            out[n] = out[at] + 1
            todo.append(n)
    return out


def misplaced(built: BuiltDesign, found: list[Link]) -> dict[str, str]:
    """Top-level instances a mate whose frames disagree puts somewhere else, with that mate's cause: of its two
    instances the one further from ground, or both when they are as far."""
    try:
        far = hops(built, found)
    except StructureError:  # no ground placed: the structure analysis says so
        far = {}
    out: dict[str, str] = {}
    for link in found:
        mate = link.source
        if not isinstance(mate, _Mate) or agree(mate, link.a, link.b, built.tolerances).passed:
            continue
        a, b = top(link.a.instance), top(link.b.instance)
        da, db = far.get(a, len(far)), far.get(b, len(far))
        for x, d in ((a, da), (b, db)):
            if d == max(da, db):
                out.setdefault(x, Cause.mate(mate.address, a, b))
    return out


class Coverage:
    name = "coverage"
    title = "Coverage"
    summary = "Nothing floats: mates, relations and fastener sets connect every instance to ground"

    def run(self, ctx: Context) -> list[Result]:
        built = ctx.built
        instances = [address for address, _, _ in built.instances()]
        out: list[Result] = []
        if not instances or built.spec.ground is None:
            return out  # the structure analysis says what is missing
        try:
            reached = hops(built, links(built)[0])
        except StructureError:
            return out
        floating = [a for a in instances if a not in reached]
        out += [result(self.name, a, False, f"{a} is floating: no mate, relation or fastener set connects it to "
                       f"{built.spec.ground}", [a]) for a in floating]  # fmt: skip
        if not floating:
            out.append(result(self.name, "ground", True, f"all {len(instances)} instances connect to {built.spec.ground}",
                              instances))  # fmt: skip
        return out
