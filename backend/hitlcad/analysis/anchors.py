"""Carry pins and selections to a new build by matching geometry, not face numbers.

Rebuilding a model renumbers its faces. A pick on the old build is carried when exactly one
component of the new build matches it: on the same part of an assembly (mating parts sit too close
to tell apart by position), the same kind (face, edge, vertex) and surface or curve type, the same orientation (a planar face's normal, a round face's axis, a straight edge's
direction), and the nearest such component to the old point within reach. Anything else, such as
no candidate in reach or a near tie, is not carried: the pick stays on the revision it was made on.
A whole part is the part of the same name; its point keeps its place relative to the part's bounding
box (a pin on top of a part that got thicker stays on top), then moves onto the part's surface.
Mesh builds (no B-rep) carry a pick to the nearest point of the new mesh within reach.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from hitlcad.analysis import cadview
from hitlcad.core.types import ComponentRef, Pick, Vec

REACH = 0.1  # of the new model's bounding-box diagonal: how far a face may move and still be "the same"
TIE = 0.1  # of the reach: a runner-up this close to the best match makes the match ambiguous
ALIGNED_DEG = 1.0


def _direction(item: Any) -> Any | None:
    """What has to stay aligned for two components to be the same: normal, axis or edge direction."""
    kind = item.geom_type.name
    if kind == "PLANE":
        return item.normal_at()
    if kind in ("CYLINDER", "CONE", "TORUS"):
        return item.axis_of_rotation.direction
    if kind == "LINE":
        return item.tangent_at(0)
    return None


def _aligned(a: Any | None, b: Any | None, signed: bool) -> bool:
    if a is None or b is None:
        return a is None and b is None
    cos = a.normalized().dot(b.normalized())
    return math.degrees(math.acos(max(-1.0, min(1.0, cos if signed else abs(cos))))) <= ALIGNED_DEG


def _reach(parts: dict[str, Any]) -> float:
    from build123d import Compound

    return REACH * Compound(children=list(parts.values())).bounding_box().diagonal


def _carry_brep(old: dict[str, Any], new: dict[str, Any], pick: Pick) -> Pick | None:
    from build123d import Vertex

    ref = pick.component
    if ref is None or ref.part not in old or ref.part not in new:
        return None
    if ref.topo == "solid":
        return _carry_part(old[ref.part], new[ref.part], pick)
    point = Vertex(*pick.point)
    lists = cadview.TOPO_LISTS[ref.topo]
    items = getattr(old[ref.part], lists)()
    if ref.index >= len(items):
        return None
    before = items[ref.index]
    kind, direction = before.geom_type, _direction(before)
    signed = kind.name == "PLANE"  # a planar face's normal has a side; axes and edge directions do not
    found = [
        (item.distance_to(point), index, item)
        for index, item in enumerate(getattr(new[ref.part], lists)())
        if item.geom_type == kind and _aligned(direction, _direction(item), signed)
    ]
    if not found:
        return None
    found.sort(key=lambda f: f[0])
    reach = _reach(new)
    distance, index, item = found[0]
    if distance > reach or (len(found) > 1 and found[1][0] - distance < TIE * reach):
        return None
    _, on_item, _ = item.distance_to_with_closest_points(point)
    moved: Vec = (on_item.X, on_item.Y, on_item.Z)
    return Pick(point=moved, normal=pick.normal, component=ComponentRef(part=ref.part, topo=ref.topo, index=index))


def _carry_part(old: Any, new: Any, pick: Pick) -> Pick:
    from build123d import Compound, Vertex

    was, now = old.bounding_box(), new.bounding_box()
    scaled = [
        lo + (p - old_lo) * (size / old_size if old_size > 0 else 1)
        for p, old_lo, old_size, lo, size in zip(
            pick.point, _xyz(was.min), _xyz(was.size), _xyz(now.min), _xyz(now.size), strict=True
        )
    ]
    _, on_part, _ = Compound(children=new.faces()).distance_to_with_closest_points(Vertex(*scaled))
    return Pick(point=(on_part.X, on_part.Y, on_part.Z), normal=pick.normal, component=pick.component)


def _xyz(v: Any) -> tuple[float, float, float]:
    return (v.X, v.Y, v.Z)


def _carry_mesh(new_mesh: Any, reach: float, pick: Pick) -> Pick | None:
    import trimesh

    closest, distance, _ = trimesh.proximity.closest_point(new_mesh, [pick.point])
    if float(distance[0]) > reach:
        return None
    return Pick(point=tuple(float(c) for c in closest[0]), normal=pick.normal, component=None)


def carry(old_geometry: Path, new_geometry: Path, picks: list[Pick]) -> list[Pick | None]:
    """Each pick on `old_geometry` placed on `new_geometry`, or None where it has no clear match."""
    brep = {g.suffix.lower() in cadview.BREP for g in (old_geometry, new_geometry)}
    if brep == {True}:
        old = cadview.parts(cadview.load_step(old_geometry), old_geometry.stem)
        new = cadview.parts(cadview.load_step(new_geometry), new_geometry.stem)
        return [_carry_brep(old, new, pick) for pick in picks]
    if brep == {False}:
        mesh = cadview.load_mesh(new_geometry).to_geometry()
        reach = REACH * float(math.dist(*mesh.bounds))
        return [_carry_mesh(mesh, reach, pick) for pick in picks]
    return [None for _ in picks]  # the backend changed between B-rep and mesh: nothing comparable
