"""B-rep items described: what a face, edge, vertex or whole part is, in words and in numbers; frames as build123d
Locations."""

from __future__ import annotations

from typing import Any

from hitlcad.geometry import cadview
from hitlcad.geometry.features import face_radius


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
        details["radius_mm"] = round(radius := face_radius(face), 4)
        label += f" r={radius:.2f} mm"
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
                   size=size, center=center, **{name: len(getattr(part, name)()) for name in ("solids", *cadview.TOPO_LISTS.values())})  # fmt: skip
    dims = " × ".join(f"{c:.2f}" for c in size)
    return f"Whole part, volume {part.volume:.1f} mm³, size {dims} mm, center {fmt(center)}"


DESCRIBE = {"face": face_text, "edge": edge_text, "vertex": vertex_text, "solid": solid_text}
