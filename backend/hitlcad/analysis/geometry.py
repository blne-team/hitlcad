"""B-rep geometry of a build: what each face, edge and vertex is, and measurements between them.

References use the numbering of the 3D view and of selections: "face 9 of lid[0]" is faces()[9] of the
lid's STEP (`parts/lid.step`), placed where instance lid[0] sits. See `Resolver` for every form.
"""

from __future__ import annotations

import math
import re
from typing import Any

from hitlcad.analysis import cadview

TOPOS = {"faces": "face", "edges": "edge", "vertices": "vertex"}
_REF = re.compile(r"^(face|edge|vertex)\s+(\d+)(?:\s+of\s+(\S+))?$", re.IGNORECASE)
_PART = re.compile(r"^part\s+(\S+)$", re.IGNORECASE)
_NUMBER = r"\s*(-?\d+(?:\.\d+)?)\s*"
_POINT = re.compile(rf"^\(?{_NUMBER},{_NUMBER},{_NUMBER}\)?$")
PARALLEL_DEG = 0.5


def location(m: Any) -> Any:
    """A 4 × 4 frame as a build123d Location."""
    from build123d import Location
    from OCP.gp import gp_Trsf

    t = gp_Trsf()
    t.SetValues(*[float(m[r][c]) for r in range(3) for c in range(4)])
    return Location(t)


def vec(v: Any, nd: int = 3) -> list[float]:
    return [round(float(c), nd) for c in (v.X, v.Y, v.Z)] if hasattr(v, "X") else [round(float(c), nd) for c in v]


def fmt(v: list[float]) -> str:
    return "(" + ", ".join(f"{c:.2f}" for c in v) + ")"


def _kind(item: Any) -> str:
    return item.geom_type.name


def face_text(face: Any, details: dict[str, Any]) -> str:
    """'Planar face, normal (0, 0, 1), area 1200.0 mm²' and the same facts in `details`."""
    kind = _kind(face)
    bb = face.bounding_box()
    details.update(geom_type=kind.lower(), area_mm2=round(face.area, 3), bbox=[vec(bb.min), vec(bb.max)],
                   center=vec((bb.min + bb.max) * 0.5))  # fmt: skip
    if face.is_planar:
        details["normal"] = n = vec(face.normal_at(), 4)
        return f"Planar face, normal {fmt(n)}, area {face.area:.1f} mm²"
    label = f"{kind.title()} face"
    if kind in ("CYLINDER", "SPHERE"):
        details["radius_mm"] = round(face.radius, 4)
        label += f" r={face.radius:.2f} mm"
    if kind in ("CYLINDER", "CONE", "TORUS"):
        axis = face.axis_of_rotation
        details["axis"] = {"point": vec(axis.position), "direction": vec(axis.direction, 4)}
        label += f", axis {fmt(vec(axis.direction, 4))}"
    return label + f", area {face.area:.1f} mm²"


def edge_text(edge: Any, details: dict[str, Any]) -> str:
    """'Circle edge r=2.25 mm, center (…), length 14.14 mm' and the same facts in `details`."""
    kind = _kind(edge)
    start, end = vec(edge.position_at(0)), vec(edge.position_at(1))
    details.update(geom_type=kind.lower(), length_mm=round(edge.length, 3), start=start, end=end)
    if kind == "LINE":
        return f"Line edge, length {edge.length:.2f} mm from {fmt(start)} to {fmt(end)}"
    if kind == "CIRCLE":
        details.update(radius_mm=round(edge.radius, 4), center=vec(edge.arc_center))
        return f"Circle edge r={edge.radius:.2f} mm, center {fmt(details['center'])}, length {edge.length:.2f} mm"
    if kind == "ELLIPSE":
        details["center"] = vec(edge.arc_center)
        return f"Ellipse edge, center {fmt(details['center'])}, length {edge.length:.2f} mm"
    return f"{kind.title()} edge, length {edge.length:.2f} mm from {fmt(start)} to {fmt(end)}"


def vertex_text(vertex: Any, details: dict[str, Any]) -> str:
    details["position"] = p = vec(vertex.center())
    return f"Vertex at {fmt(p)}"


def solid_text(part: Any, details: dict[str, Any]) -> str:
    """'Whole part, volume 7200.0 mm³, size 40.00 × 30.00 × 6.00 mm, center (…)' and the same facts,
    with its topology, in `details`."""
    bb = part.bounding_box()
    size, center = vec(bb.max - bb.min), vec((bb.min + bb.max) * 0.5)
    details.update(volume_mm3=round(part.volume, 3), area_mm2=round(part.area, 3), bbox=[vec(bb.min), vec(bb.max)],
                   size=size, center=center, **{name: len(getattr(part, name)()) for name in ("solids", *TOPOS)})  # fmt: skip
    dims = " × ".join(f"{c:.2f}" for c in size)
    return f"Whole part, volume {part.volume:.1f} mm³, size {dims} mm, center {fmt(center)}"


DESCRIBE = {"face": face_text, "edge": edge_text, "vertex": vertex_text, "solid": solid_text}


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
        from hitlcad.analysis.selection import leaf_of

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
                raise ValueError(f"no part {m.group(1)!r}; parts: {', '.join(self.built.parts)}")
            return f"part {m.group(1)} (its own frame)", part.features.exact(), None
        if m := _REF.match(text):
            topo, index, owner = m.group(1).lower(), int(m.group(2)), m.group(3)
            if owner is None:
                raise ValueError(f"say which instance: '{topo} {index} of lid[0]'")
            leaf = self._leaf(owner)
            items = leaf.part.features.topology(cadview.TOPO_LISTS[topo])
            if index >= len(items):
                raise ValueError(f"{leaf.path} has {len(items)} {topo}s (0–{len(items) - 1})")
            return f"{topo} {index} of {leaf.path}", items[index].moved(location(leaf.transform)), None
        if "." in text:
            from hitlcad.core.address import End

            body, _, last = text.rpartition(".")
            name, _, k = last.partition("[")
            frames = self.built.frames(str(End.parse(f"{body}.{name}")))
            index = int(k.rstrip("]")) if k else 0
            matching = [f for f in frames if f.index == index]
            if not matching:
                raise ValueError(f"{text}: no frame {index}")
            if len(matching) > 1:
                raise ValueError(f"{text} names {len(matching)} instances; say which, e.g. {matching[0].address}")
            f = matching[0]
            o, z = fr.origin(f.frame), fr.z_axis(f.frame)
            return f"connector {f.address}" + (f"[{index}]" if k else ""), Vertex(*o), Vector(*z)
        leaf = self._leaf(text)
        return f"instance {leaf.path}", leaf.part.features.exact().moved(location(leaf.transform)), None


def list_geometry(built: Any, target: str, kind: str = "faces") -> str:
    """Every face, edge or vertex of a part (its own frame: "part lid") or an instance (placed: "lid[0]"),
    numbered as picks and the measuring tool number them."""
    if kind not in TOPOS:
        raise ValueError(f"kind must be one of {', '.join(TOPOS)}")
    name, shape, _ = Resolver(built).resolve(target)
    counts = ", ".join(f"{len(getattr(shape, k)())} {k}" for k in TOPOS)
    lines = [f"{name}: {counts}"]
    topo = TOPOS[kind]
    lines += [f"  {topo} {i}: {DESCRIBE[topo](item, {})}" for i, item in enumerate(getattr(shape, kind)())]
    return "\n".join(lines)


def _direction(item: Any) -> tuple[str, Any] | None:
    """The normal of a planar face or the direction of a straight edge; None for anything else."""
    from build123d import Edge, Face

    if isinstance(item, Face) and item.is_planar:
        return "normal", item.normal_at()
    if isinstance(item, Edge) and _kind(item) == "LINE":
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
