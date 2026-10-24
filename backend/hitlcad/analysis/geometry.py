"""B-rep geometry of a build: what each face, edge and vertex is, measurements between them, and round holes.

References use the numbering of the 3D view and of selections: "face 9 of model" is faces()[9] of
part `model` in build123d (the parts of a STEP are its solids, as the viewer's tree shows them).
A model with one part can drop "of model". A whole part is "part lid", a point "(x, y, z)" in mm.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from hitlcad.analysis import cadview

TOPOS = {"faces": "face", "edges": "edge", "vertices": "vertex"}
_REF = re.compile(r"^(face|edge|vertex)\s+(\d+)(?:\s+of\s+(\S+))?$", re.IGNORECASE)
_PART = re.compile(r"^part\s+(\S+)$", re.IGNORECASE)
_NUMBER = r"\s*(-?\d+(?:\.\d+)?)\s*"
_POINT = re.compile(rf"^\(?{_NUMBER},{_NUMBER},{_NUMBER}\)?$")
PARALLEL_DEG = 0.5
FULL_CIRCLE = 0.95  # of 2π: a cylindrical cut that goes this far round is a hole, not a fillet or slot end


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


class StepModel:
    """A built STEP with its parts, addressed the way the 3D view and selections number them."""

    def __init__(self, step: Path) -> None:
        self.parts = cadview.parts(cadview.load_step(step), step.stem)

    def part(self, name: str | None) -> tuple[str, Any]:
        """A part by its name or path; may be omitted when the model has one part."""
        if name is None:
            if len(self.parts) != 1:
                raise ValueError(f"the model has {len(self.parts)} parts; say which: {', '.join(self._names())}")
            return next(iter(self.parts.items()))
        hits = [(path, shape) for path, shape in self.parts.items() if name in (path, path.rsplit("/", 1)[-1])]
        if len(hits) != 1:
            raise ValueError(f"no single part named {name!r}; parts: {', '.join(self._names())}")
        return hits[0]

    def resolve(self, ref: str) -> tuple[str, Any]:
        """('face 9 of model', the build123d Face) for a reference, the part for 'part lid', or a Vertex
        for '(x, y, z)'."""
        from build123d import Vertex

        text = ref.strip()
        if m := _POINT.match(text):
            point = tuple(float(c) for c in m.groups())
            return f"point {fmt(list(point))}", Vertex(*point)
        if m := _PART.match(text):
            path, shape = self.part(m.group(1))
            return f"part {path.rsplit('/', 1)[-1]}", shape
        m = _REF.match(text)
        if not m:
            raise ValueError(f"{ref!r} is not a reference like 'face 9', 'edge 3 of block', 'part lid' or '(0, 0, 10)'")
        topo, index, name = m.group(1).lower(), int(m.group(2)), m.group(3)
        path, shape = self.part(name)
        items = getattr(shape, cadview.TOPO_LISTS[topo])()
        if index >= len(items):
            raise ValueError(f"part {path.rsplit('/', 1)[-1]} has {len(items)} {topo}s (0–{len(items) - 1})")
        return f"{topo} {index} of {path.rsplit('/', 1)[-1]}", items[index]

    def _names(self) -> list[str]:
        return [path.rsplit("/", 1)[-1] for path in self.parts]


def list_geometry(step: Path, kind: str = "faces", part: str | None = None) -> str:
    """Every face, edge or vertex of one part (or all parts), numbered as the 3D view numbers them."""
    if kind not in TOPOS:
        raise ValueError(f"kind must be one of {', '.join(TOPOS)}")
    model = StepModel(step)
    selected = [model.part(part)] if part else list(model.parts.items())
    lines = []
    for path, shape in selected:
        leaf = path.rsplit("/", 1)[-1]
        counts = ", ".join(f"{len(getattr(shape, name)())} {name}" for name in TOPOS)
        lines.append(f"Part {leaf}: {counts}")
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


def measure(step: Path, a: str, b: str) -> str:
    """Minimum distance between two references, plus the angle (and parallel gap) where they have directions."""
    model = StepModel(step)
    (name_a, item_a), (name_b, item_b) = model.resolve(a), model.resolve(b)
    distance, point_a, point_b = item_a.distance_to_with_closest_points(item_b)
    lines = [
        f"{name_a} to {name_b}: minimum distance {distance:.3f} mm, between {fmt(vec(point_a))} and {fmt(vec(point_b))}"
    ]
    dir_a, dir_b = _direction(item_a), _direction(item_b)
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


@dataclass(frozen=True)
class Hole:
    diameter: float
    depth: float


@lru_cache(maxsize=32)
def find_holes(step: Path) -> list[Hole]:
    """Complete round holes (through or blind): inward-facing cylindrical faces that go all the way round."""
    from build123d import Vector

    groups: dict[tuple[float, tuple[float, ...], tuple[float, ...]], list] = defaultdict(list)
    for face in cadview.load_step(step).faces():
        if face.geom_type.name != "CYLINDER":
            continue
        axis = face.axis_of_rotation
        d = axis.direction.normalized()
        p = face.position_at(0.5, 0.5)
        radial = (p - axis.position) - d * (p - axis.position).dot(d)
        if face.normal_at(p).dot(radial) >= 0:
            continue  # faces away from its axis: a boss or a rounded outside corner
        direction = tuple(round(c, 6) + 0.0 for c in (d.X, d.Y, d.Z))
        if next(c for c in direction if c != 0) < 0:
            direction = tuple(-c + 0.0 for c in direction)
        origin = axis.position - d * axis.position.dot(d)
        groups[
            (round(face.radius, 4), direction, tuple(round(c, 3) + 0.0 for c in (origin.X, origin.Y, origin.Z)))
        ].append(face)
    holes = []
    for (radius, direction, origin), faces in groups.items():
        along = [(v.center() - Vector(*origin)).dot(Vector(*direction)) for f in faces for v in f.vertices()]
        depth = max(along) - min(along)
        if depth > 0 and sum(f.area for f in faces) / (radius * depth) >= FULL_CIRCLE * 2 * math.pi:
            holes.append(Hole(diameter=2 * radius, depth=depth))
    return holes
