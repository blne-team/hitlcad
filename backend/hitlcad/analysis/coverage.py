"""Coverage: mates, relations and fastener sets connect every instance to `ground`. An instance nothing
connects is floating: placed, but related to nothing (the real-CAD "under-defined component")."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hitlcad.analysis.base import Context, pair, result, structural
from hitlcad.analysis.fasteners import stacks
from hitlcad.analysis.model import BuiltDesign, PlacedFrame, StructureError
from hitlcad.analysis.relations import relation_pairs
from hitlcad.core import frames as fr
from hitlcad.core.address import InstancePath
from hitlcad.core.report import Result


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
    view = _View(built)
    out, errors = [], []
    for mate in built.spec.mates:
        try:
            out += [Link(fa, fb, mate.type, mate) for fa, fb in pair(built.frames(mate.a), built.frames(mate.b))]
        except StructureError as e:
            errors.append(structural(mate.address, e, [mate.a, mate.b]))
    for rel in built.spec.relations:
        try:
            out += [Link(fa, fb, rel.type, rel) for fa, fb in relation_pairs(view, rel)]
        except StructureError as e:
            errors.append(structural(rel.address, e, [rel.a, rel.b]))
    for fs in built.spec.fasteners:
        try:
            seats, all_stacks = stacks(view, fs)
        except StructureError as e:
            errors.append(structural(fs.address, e, [fs.fastener, *fs.through, fs.into]))
            continue
        for seat, stack in zip(seats, all_stacks, strict=True):
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


class _View:
    """What the link helpers read of an analysis context: the design and its tolerances."""

    def __init__(self, built: BuiltDesign) -> None:
        self.built, self.tol = built, built.tolerances


def top(instance: str) -> str:
    return str(InstancePath.parse(instance).head)


class Coverage:
    name = "coverage"

    def run(self, ctx: Context) -> list[Result]:
        built = ctx.built
        instances = [address for address, _, _ in built.instances()]
        out: list[Result] = []
        if not instances or built.spec.ground is None:
            return out  # the structure analysis says what is missing
        try:
            grounded = [a for a, _ in built.select(InstancePath.parse(built.spec.ground).head)]
        except StructureError:
            return out
        found, _ = links(built)
        neighbours: dict[str, set[str]] = {a: set() for a in instances}
        for link in found:
            a, b = top(link.a.instance), top(link.b.instance)
            neighbours[a].add(b)
            neighbours[b].add(a)
        reached, todo = set(grounded), list(grounded)
        while todo:
            for n in neighbours[todo.pop()] - reached:
                reached.add(n)
                todo.append(n)
        floating = [a for a in instances if a not in reached]
        out += [result(self.name, a, False, f"{a} is floating: no mate, relation or fastener set connects it to "
                       f"{built.spec.ground}", [a]) for a in floating]  # fmt: skip
        if not floating:
            out.append(result(self.name, "ground", True, f"all {len(instances)} instances connect to {built.spec.ground}",
                              instances))  # fmt: skip
        return out
