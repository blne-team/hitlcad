"""Measuring a build: every face, edge and vertex of a part or instance, and distances and angles between them.

References use the numbering of the 3D view and of selections: "face 9 of lid[0]" is faces()[9] of the
lid's STEP (`parts/lid.step`), placed where instance lid[0] sits. See `Resolver` for every form.
"""

from __future__ import annotations

import math
import re
from typing import Any

from hitlcad.geometry import cadview
from hitlcad.geometry.brep import DESCRIBE, fmt, location, vec
from hitlcad.kernel.errors import NOT_FOUND_GEOMETRY, REQUEST_INVALID

_REF = re.compile(r"^(face|edge|vertex)\s+(\d+)(?:\s+of\s+(\S+))?$", re.IGNORECASE)
_PART = re.compile(r"^part\s+(\S+)$", re.IGNORECASE)
_NUMBER = r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*"
_POINT = re.compile(rf"^\(?{_NUMBER},{_NUMBER},{_NUMBER}\)?$")
PARALLEL_DEG = 0.5


class Resolver:
    """Addresses of a built design as B-rep items in model space (or a part's own frame):

    "face 9 of lid[0]"   a face of an instance, placed      "face 9 of lid"   … of the only instance
    "lid[0]"             the whole instance, placed          "part lid"        the part in its own frame
    "lid.screws[2]"      a connector frame: its origin (and Z as its direction)
    "(0, 0, 10)"         a point, mm
    """

    def __init__(self, built: Any) -> None:
        self.built = built

    def _leaf(self, address: str) -> Any:
        from hitlcad.review.selection import leaf_of

        return leaf_of(self.built, address)

    def resolve(self, ref: str) -> tuple[str, Any, Any | None]:
        """(name, the item, a direction for connector frames)."""
        from build123d import Vector, Vertex

        from hitlcad.core import frames as fr

        text = ref.strip()
        if m := _POINT.match(text):
            point = tuple(float(c) for c in m.groups())
            return f"point {fmt(list(point))}", Vertex(*point), None
        if m := _PART.match(text):
            part = self.built.parts.get(m.group(1))
            if part is None:
                raise NOT_FOUND_GEOMETRY(f"no part {m.group(1)!r}; parts: {', '.join(self.built.parts)}")
            return f"part {m.group(1)} (its own frame)", part.features.exact(), None
        if m := _REF.match(text):
            topo, index, owner = m.group(1).lower(), int(m.group(2)), m.group(3)
            if owner is None:
                raise REQUEST_INVALID(f"say which instance: '{topo} {index} of lid[0]'")
            leaf = self._leaf(owner)
            items = leaf.part.features.topology(cadview.TOPO_LISTS[topo])
            if index >= len(items):
                raise NOT_FOUND_GEOMETRY(f"{leaf.path} has {len(items)} {topo}s (0–{len(items) - 1})")
            return f"{topo} {index} of {leaf.path}", items[index].moved(location(leaf.transform)), None
        if "." in text:
            body, _, last = text.rpartition(".")
            name, _, k = last.partition("[")
            frames = self.built.frames(f"{body}.{name}")
            if k and not k.rstrip("]").isdigit():
                raise REQUEST_INVALID(f"{text!r}: a frame of a pattern is connector[k], k a number")
            index = int(k.rstrip("]")) if k else 0
            matching = [f for f in frames if f.index == index]
            if not matching:
                raise NOT_FOUND_GEOMETRY(f"{text}: no frame {index}")
            if len(matching) > 1:
                raise REQUEST_INVALID(f"{text} names {len(matching)} instances; say which, e.g. {matching[0].address}")
            f = matching[0]
            o, z = fr.origin(f.frame), fr.z_axis(f.frame)
            return f"connector {f.address}" + (f"[{index}]" if k else ""), Vertex(*o), Vector(*z)
        leaf = self._leaf(text)
        return f"instance {leaf.path}", leaf.part.features.exact().moved(location(leaf.transform)), None


def list_geometry(built: Any, target: str, kind: str = "faces") -> str:
    """Every face, edge or vertex of a part (its own frame: "part lid") or an instance (placed: "lid[0]"),
    numbered as picks and the measuring tool number them."""
    topos = {plural: topo for topo, plural in cadview.TOPO_LISTS.items()}
    if kind not in topos:
        raise REQUEST_INVALID(f"kind must be one of {', '.join(topos)}")
    name, shape, _ = Resolver(built).resolve(target)
    counts = ", ".join(f"{len(getattr(shape, k)())} {k}" for k in topos)
    lines = [f"{name}: {counts}"]
    topo = topos[kind]
    lines += [f"  {topo} {i}: {DESCRIBE[topo](item, {})}" for i, item in enumerate(getattr(shape, kind)())]
    return "\n".join(lines)


def _direction(item: Any) -> tuple[str, Any] | None:
    """The normal of a planar face or the direction of a straight edge; None for anything else."""
    from build123d import Edge, Face

    if isinstance(item, Face) and item.is_planar:
        return "normal", item.normal_at()
    if isinstance(item, Edge) and item.geom_type.name == "LINE":
        return "line", item.tangent_at(0)
    return None


def measure(built: Any, a: str, b: str) -> str:
    """Minimum distance between two addresses, plus the angle (and parallel gap) where they have directions."""
    resolver = Resolver(built)
    (name_a, item_a, dir_a_conn), (name_b, item_b, dir_b_conn) = resolver.resolve(a), resolver.resolve(b)
    distance, point_a, point_b = item_a.distance_to_with_closest_points(item_b)
    lines = [
        f"{name_a} to {name_b}: minimum distance {distance:.3f} mm, between {fmt(vec(point_a))} and {fmt(vec(point_b))}"
    ]
    dir_a = ("line", dir_a_conn) if dir_a_conn is not None else _direction(item_a)
    dir_b = ("line", dir_b_conn) if dir_b_conn is not None else _direction(item_b)
    if dir_a and dir_b:
        cos = max(-1.0, min(1.0, dir_a[1].normalized().dot(dir_b[1].normalized())))
        between = math.degrees(math.acos(cos))
        if (dir_a[0], dir_b[0]) == ("normal", "normal"):
            lines.append(f"Angle between the face normals: {between:.2f}°")
            if between < PARALLEL_DEG or between > 180 - PARALLEL_DEG:
                gap = abs((item_b.center() - item_a.center()).dot(dir_a[1].normalized()))
                lines.append(f"The faces are parallel; the gap between their planes is {gap:.3f} mm")
        elif (dir_a[0], dir_b[0]) == ("line", "line"):
            lines.append(f"Angle between the edges: {min(between, 180 - between):.2f}°")
        else:
            lines.append(f"Angle between the edge and the face's plane: {abs(90 - between):.2f}°")
    return "\n".join(lines)
