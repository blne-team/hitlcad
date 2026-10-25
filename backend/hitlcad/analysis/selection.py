"""Describe what the human clicked in a 3D view, in mm, Z up.

On a build: a face, edge or vertex of one instance (numbered as `faces()` / `edges()` / `vertices()` of
its part's STEP), the whole instance, or a point on it and the object there, where its part names several;
with the connector whose feature a face is. On a loose model file: the same for the file's own parts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from hitlcad.analysis import cadview
from hitlcad.analysis.features import Features
from hitlcad.analysis.geometry import DESCRIBE, fmt, location, vec
from hitlcad.analysis.model import BuiltDesign, Leaf, PartGeometry
from hitlcad.core import frames as fr
from hitlcad.core.types import ComponentRef, SelectionItem, Vec


def topology(features: Features, ref: ComponentRef) -> Any:
    if ref.topo == "solid":
        return features.exact()
    items = features.topology(cadview.TOPO_LISTS[ref.topo])
    if ref.index >= len(items):
        raise LookupError(f"{ref.instance} has {len(items)} {ref.topo}s, not {ref.index + 1}")
    return items[ref.index]


def leaf_of(built: BuiltDesign, instance: str) -> Leaf:
    leaves = built.leaves_of(instance)
    if len(leaves) != 1:
        raise LookupError(f"{instance} is a component instance; pick one of its parts")
    return leaves[0]


def connector_of(part: PartGeometry, item: Any, tol: Any) -> str | None:
    """The connector (and frame, in a pattern) whose feature a part-frame face is."""
    kind = item.geom_type.name
    for name, spec in part.spec.connectors.items():
        frames = part.frames.get(name, [])
        for k, frame in enumerate(frames):
            o, z = fr.origin(frame), fr.z_axis(frame)
            if spec.kind in ("hole", "shaft") and kind == "CYLINDER":
                axis = item.axis_of_rotation
                d = vec(axis.direction)
                if (min(fr.angle_deg(d, z), fr.angle_deg(d, -z)) <= tol.angle_deg
                        and fr.distance_to_line(o, vec(axis.position), d) <= tol.position_mm):  # fmt: skip
                    return name + (f"[{k}]" if len(frames) > 1 else "")
            if (spec.kind == "face" and kind == "PLANE" and fr.angle_deg(vec(item.normal_at()), z) <= tol.angle_deg
                    and abs(fr.along(vec(item.center()), o, z)) <= tol.position_mm):  # fmt: skip
                return name + (f"[{k}]" if len(frames) > 1 else "")
    return None


def describe_build_pick(built: BuiltDesign, point: Vec, normal: Vec | None, ref: ComponentRef | None) -> SelectionItem:
    """A click on a build: the exact component of an instance (a face, edge or vertex of a B-rep, or the
    whole instance), or a point on one."""
    if ref is None:
        leaves = built.leaves()
        k, where = _nearest([(leaf.part.features, leaf.transform) for leaf in leaves], point)
        leaf = leaves[k]
        details = {"instance": leaf.path, "part": leaf.part.name}
        label = _point_label(f"{where} of {leaf.path}" if where else leaf.path, point, normal)
        return SelectionItem(point=point, normal=normal, kind="point", label=label, details=details, instance=leaf.path)
    leaf = leaf_of(built, ref.instance)
    item = topology(leaf.part.features, ref)
    placed = item.moved(location(leaf.transform))
    details: dict[str, Any] = {"instance": leaf.path, "part": leaf.part.name}
    if ref.topo != "solid":
        details[ref.topo] = ref.index
    text = DESCRIBE[ref.topo](placed, details)
    connector = connector_of(leaf.part, item, built.tolerances) if ref.topo == "face" else None
    name = f"instance {leaf.path}" if ref.topo == "solid" else f"{ref.topo} {ref.index} of {leaf.path}"
    address = f"{leaf.path}.{connector}" if connector else None
    label = (f"{address}: " if address else "") + f"{text} ({name}) at {fmt(list(point))}"
    canonical = ref.model_copy(update={"instance": leaf.path})
    return SelectionItem(point=point, normal=normal, kind=ref.topo, label=label, details=details, instance=leaf.path,
                         component=canonical, connector=address)  # fmt: skip


def describe_file_pick(path: Path, point: Vec, normal: Vec | None, ref: ComponentRef | None) -> SelectionItem:
    """A click in the file viewer, on the file's parts as `cadview.open_model` opens them: the exact component
    of a part, or a point on one."""
    parts = cadview.open_model(path)
    if ref is None:
        k, where = _nearest([(features, fr.IDENTITY) for _, features in parts], point)
        name = parts[k][1].name
        label = _point_label(f"{where} of {name}" if where else name, point, normal)
        return SelectionItem(point=point, normal=normal, kind="point", label=label, details={"part": name})
    hits = [(view_id, f) for view_id, f in parts if view_id.strip("/").split("/", 1)[-1] == ref.instance]
    if len(hits) != 1:
        raise LookupError(f"no part {ref.instance}")
    ((view_id, features),) = hits
    item = topology(features, ref)
    details: dict[str, Any] = {"part": view_id} if ref.topo == "solid" else {"part": view_id, ref.topo: ref.index}
    name = f"part {ref.instance}" if ref.topo == "solid" else f"{ref.topo} {ref.index} of {ref.instance}"
    label = f"{DESCRIBE[ref.topo](item, details)} ({name}) at {fmt(list(point))}"
    return SelectionItem(point=point, normal=normal, kind=ref.topo, label=label, details=details, component=ref)


def _nearest(placed: list[tuple[Features, fr.Frame]], point: Vec) -> tuple[int, str | None]:
    """Which placed part a point is on (the nearest), and the object of it there, where it has several."""
    local = [(np.linalg.inv(transform) @ np.append(point, 1.0))[:3] for _, transform in placed]
    gaps = [float(f.probe.nearest(p[None])[0][0]) for (f, _), p in zip(placed, local, strict=True)]
    k = int(np.argmin(gaps))
    return k, placed[k][0].named_at(local[k])


def _point_label(where: str, point: Vec, normal: Vec | None) -> str:
    label = f"Point on {where} at {fmt(list(point))}"
    return label + (f", surface normal {fmt([round(c, 2) for c in normal])}" if normal else "")
