"""Carrying picks and pins to a new build. Face numbers change when a part is rebuilt, so picks are carried
by what they are, each kind by one rule:

- A pick on a connector's feature keeps its connector: it lands on that connector's feature in the new
  build (the same frame of a pattern).
- A pick on a whole instance keeps the instance; its point keeps its place relative to the instance's
  bounding box, on the surface.
- Any other face, edge or vertex is matched by geometry within its part, in the part's own frame: the
  same kind and surface or curve type, the same orientation, the nearest within reach of the old point,
  and clearly nearer than any other candidate.
- A point on a mesh moves to the nearest point of the same instance within reach.

Anything without a match stays on the revision it was made on; the panel and the agent say so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from hitlcad.analysis import cadview
from hitlcad.analysis.geometry import vec
from hitlcad.analysis.model import BuiltDesign, Leaf, StructureError
from hitlcad.analysis.selection import connector_of, leaf_of, topology
from hitlcad.core import frames as fr
from hitlcad.core.types import ComponentRef, Pick, Vec


@dataclass(frozen=True)
class AnchorSettings:
    reach: float  # × the part's bounding-box diagonal
    tie: float  # × the reach
    aligned_deg: float


def _direction(item: Any) -> Any | None:
    kind = item.geom_type.name
    if kind == "PLANE":
        return item.normal_at()
    if kind in ("CYLINDER", "CONE", "TORUS"):
        return item.axis_of_rotation.direction
    if kind == "LINE":
        return item.tangent_at(0)
    return None


def _aligned(a: Any | None, b: Any | None, signed: bool, limit: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    cos = a.normalized().dot(b.normalized())
    return math.degrees(math.acos(max(-1.0, min(1.0, cos if signed else abs(cos))))) <= limit


def _to(m: fr.Frame, p: Any) -> Vec:
    return tuple(float(v) for v in (m @ np.append(np.asarray(p, dtype=float), 1.0))[:3])  # type: ignore[return-value]


def _on(item: Any, local: Vec) -> Vec:
    from build123d import Vertex

    _, on_item, _ = item.distance_to_with_closest_points(Vertex(*local))
    return (on_item.X, on_item.Y, on_item.Z)


def _carry_connector(old: Leaf, new: Leaf, ref: ComponentRef, connector: str, local: Vec, tol: Any) -> Pick | None:
    """The face of the new part that is the same connector's feature."""
    items = getattr(new.part.shape, cadview.TOPO_LISTS["face"])()
    for index, item in enumerate(items):
        if connector_of(new.part, item, tol) == connector:
            moved = _to(new.transform, _on(item, local))
            return Pick(point=moved, normal=None, component=ref.model_copy(update={"index": index}))
    return None


def _carry_geometry(old: Leaf, new: Leaf, ref: ComponentRef, local: Vec, settings: AnchorSettings) -> Pick | None:
    from build123d import Vertex

    before = topology(old.part.shape, ref)
    kind, direction = before.geom_type, _direction(before)
    signed = kind.name == "PLANE"  # a planar face's normal has a side; axes and edge directions do not
    point = Vertex(*local)
    found = sorted(
        ((item.distance_to(point), index, item)
         for index, item in enumerate(getattr(new.part.shape, cadview.TOPO_LISTS[ref.topo])())
         if item.geom_type == kind and _aligned(direction, _direction(item), signed, settings.aligned_deg)),
        key=lambda f: f[0],
    )  # fmt: skip
    if not found:
        return None
    reach = settings.reach * new.part.shape.bounding_box().diagonal
    distance, index, item = found[0]
    if distance > reach or (len(found) > 1 and found[1][0] - distance < settings.tie * reach):
        return None
    return Pick(
        point=_to(new.transform, _on(item, local)), normal=None, component=ref.model_copy(update={"index": index})
    )


def _carry_instance(old: Leaf, new: Leaf, ref: ComponentRef, local: Vec) -> Pick:
    from build123d import Compound

    was, now = old.part.shape.bounding_box(), new.part.shape.bounding_box()
    scaled = [lo + (p - old_lo) * (size / old_size if old_size > 0 else 1)
              for p, old_lo, old_size, lo, size in zip(local, vec(was.min), vec(was.size), vec(now.min), vec(now.size),
                                                       strict=True)]  # fmt: skip
    on = _on(Compound(children=new.part.shape.faces()), tuple(scaled))  # type: ignore[arg-type]
    return Pick(point=_to(new.transform, on), normal=None, component=ref)


def _carry_point(
    old: BuiltDesign, new: BuiltDesign, pick: Pick, instance: str, settings: AnchorSettings
) -> Pick | None:
    import trimesh

    try:
        before, after = leaf_of(old, instance), leaf_of(new, instance)
    except (StructureError, LookupError):
        return None
    local = _to(np.linalg.inv(before.transform), pick.point)
    closest, distance, _ = trimesh.proximity.closest_point(after.part.mesh, [local])
    reach = settings.reach * float(math.dist(*after.part.mesh.bounds))
    if float(distance[0]) > reach:
        return None
    return Pick(point=_to(after.transform, closest[0]), normal=pick.normal, component=None)


def carry(old: BuiltDesign, new: BuiltDesign, picks: list[tuple[Pick, str | None, str | None]],
          settings: AnchorSettings) -> list[Pick | None]:  # fmt: skip
    """Each (pick, its connector, its instance) placed on `new`, or None where it has no clear match."""
    out: list[Pick | None] = []
    for pick, connector, instance in picks:
        ref = pick.component
        if ref is None:
            out.append(_carry_point(old, new, pick, instance, settings) if instance else None)
            continue
        try:
            before, after = leaf_of(old, ref.instance), leaf_of(new, ref.instance)
        except (StructureError, LookupError):
            out.append(None)
            continue
        if before.part.shape is None or after.part.shape is None:
            out.append(None)
            continue
        local = _to(np.linalg.inv(before.transform), pick.point)
        if ref.topo == "solid":
            out.append(_carry_instance(before, after, ref, local))
        elif connector:
            out.append(_carry_connector(before, after, ref, connector.rpartition(".")[2], local, new.tolerances))
        else:
            out.append(_carry_geometry(before, after, ref, local, settings))
    return out
