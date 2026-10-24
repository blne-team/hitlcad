"""Measuring the feature a connector frame stands for, on the part's B-rep in its own frame.

A frame with no matching feature fails, so a frame cannot drift away from the geometry it describes.
"""

from __future__ import annotations

from typing import Any

from hitlcad.core import frames as fr
from hitlcad.core.structure import Measured, Tolerances


def _vec(v: Any) -> list[float]:
    return [v.X, v.Y, v.Z]


def measure_round(shape: Any, frame: fr.Frame, tol: Tolerances, *, inward: bool) -> Measured:
    """The hole (inward) or shaft coaxial with the frame, adjacent to its origin: a hole lies below the
    origin (it opens along +Z), a shaft above it (it runs from its shoulder along +Z). Of features that
    start equally close, a hole is the innermost (the space at the axis) and a shaft the outermost (what
    meets the hole), e.g. a bearing's bore, not the gap between its races."""
    o, z = fr.origin(frame), fr.z_axis(frame)
    spans: dict[float, list[float]] = {}
    for face in shape.faces():
        if face.geom_type.name != "CYLINDER":
            continue
        axis = face.axis_of_rotation
        d = _vec(axis.direction)
        if min(fr.angle_deg(d, z), fr.angle_deg(d, -z)) > tol.angle_deg:
            continue
        if fr.distance_to_line(o, _vec(axis.position), d) > tol.position_mm:
            continue
        p = face.position_at(0.5, 0.5)
        radial = (p - axis.position) - axis.direction * (p - axis.position).dot(axis.direction)
        if (face.normal_at(p).dot(radial) < 0) != inward:
            continue
        ts = [fr.along(_vec(v.center()), o, z) for v in face.vertices()]
        if not ts:
            continue
        span = spans.setdefault(round(face.radius, 6), [min(ts), max(ts)])
        span[0], span[1] = min(span[0], *ts), max(span[1], *ts)
    what = "hole" if inward else "shaft"
    if inward:
        near = [(r, s, -s[1]) for r, s in spans.items() if s[1] <= tol.position_mm]
    else:
        near = [(r, s, s[0]) for r, s in spans.items() if s[0] >= -tol.position_mm]
    closest = min((gap for _, _, gap in near), default=None)
    tied = [(r, s) for r, s, gap in near if closest is not None and gap <= closest + tol.position_mm]
    hit = (min if inward else max)(tied, key=lambda rs: rs[0], default=None)
    if hit is None:
        side = "below" if inward else "above"
        return Measured(status="fail", detail=f"no {what} on the frame's axis {side} its origin")
    radius, (t0, t1) = hit
    length = t1 - t0
    gap = -t1 if inward else t0
    detail = f"Ø{2 * radius:.3f} × {length:.2f} {'deep' if inward else 'long'}"
    if gap > tol.position_mm:
        detail += f", starting {gap:.2f} mm from the origin"
    return Measured(status="pass", detail=detail, values={"diameter": 2 * radius, "length": length, "offset": gap})


def measure_face(shape: Any, frame: fr.Frame, tol: Tolerances) -> Measured:
    """The planar face whose outward normal is the frame's Z, with the origin on its plane and inside its
    outline (a mate connector may sit at the centre of a rim or an annulus, where there is no material)."""
    from build123d import Face, Vertex

    o, z = fr.origin(frame), fr.z_axis(frame)
    point = Vertex(*o)
    for face in shape.faces():
        if not face.is_planar or fr.angle_deg(_vec(face.normal_at()), z) > tol.angle_deg:
            continue
        if abs(fr.along(_vec(face.center()), o, z)) > tol.position_mm:
            continue
        if Face(face.outer_wire()).distance_to(point) > tol.position_mm:
            continue
        return Measured(status="pass", detail=f"face of {face.area:.1f} mm²", values={"area": face.area})
    return Measured(
        status="fail", detail="no planar face with Z as its outward normal and the origin inside its outline"
    )
