"""Describe what the human clicked in a 3D view, in mm, Z up.

On a build: a face, edge or vertex of one instance (numbered as `faces()` / `edges()` / `vertices()` of
its part's STEP), the whole instance, or, on mesh parts, a point on a named object; with the connector
whose feature it is. On a loose model file: the same for the file's own parts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from hitlcad.analysis import cadview
from hitlcad.analysis.features import BrepFeatures, Features
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
    if not hasattr(item, "geom_type"):
        return None
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
    """A click on a build: the exact component of a B-rep instance, or a point on a mesh instance."""
    if ref is not None:
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
    return describe_build_point(built, point, normal)


def describe_build_point(built: BuiltDesign, point: Vec, normal: Vec | None) -> SelectionItem:
    """A point on a mesh build: the instance it is on and, where the part names its objects, the object."""
    import trimesh

    leaves = built.leaves()
    distances = [float(trimesh.proximity.closest_point(leaf.mesh, [point])[1][0]) for leaf in leaves]
    leaf = leaves[int(np.argmin(distances))]
    local = (np.linalg.inv(leaf.transform) @ np.append(point, 1.0))[:3]
    where = leaf.path
    if leaf.part.objects_file is not None:
        names, pieces = zip(*cadview.objects(cadview.load_mesh(leaf.part.objects_file)), strict=True)
        nearest = int(np.argmin([float(np.min(np.linalg.norm(p.vertices - local, axis=1))) for p in pieces]))
        where = f"{names[nearest]} of {leaf.path}"
    details = {"instance": leaf.path, "part": leaf.part.name}
    label = f"Point on {where} at {fmt(list(point))}"
    if normal:
        label += f", surface normal {fmt([round(c, 2) for c in normal])}"
    return SelectionItem(point=point, normal=normal, kind="point", label=label, details=details, instance=leaf.path)


# --------------------------------------------------------------------------- loose model files


def _file_part(paths: dict[str, Any], instance: str) -> tuple[str, Any]:
    hits = [(p, s) for p, s in paths.items() if p.strip("/").split("/", 1)[-1] == instance]
    if len(hits) != 1:
        raise LookupError(f"no part {instance}")
    return hits[0]


def describe_file_pick(path: Path, point: Vec, normal: Vec | None, ref: ComponentRef | None) -> SelectionItem:
    """A click in the file viewer: STEP picks name their exact component, mesh picks are a point."""
    if path.suffix.lower() in cadview.BREP:
        if ref is None:
            raise ValueError("a STEP pick must name its face, edge, vertex or part")
        part_path, shape = _file_part(cadview.parts(cadview.load_step(path), path.stem), ref.instance)
        item = topology(BrepFeatures(shape), ref)
        details: dict[str, Any] = (
            {"part": part_path} if ref.topo == "solid" else {"part": part_path, ref.topo: ref.index}
        )
        name = f"part {ref.instance}" if ref.topo == "solid" else f"{ref.topo} {ref.index} of {ref.instance}"
        label = f"{DESCRIBE[ref.topo](item, details)} ({name}) at {fmt(list(point))}"
        return SelectionItem(point=point, normal=normal, kind=ref.topo, label=label, details=details, component=ref)
    if ref is not None:
        raise ValueError("meshes have no faces, edges, vertices or parts to pick")
    return _describe_mesh_file(path, point, normal)


def _describe_mesh_file(mesh_path: Path, point: Vec, normal: Vec | None) -> SelectionItem:
    import trimesh

    scene = cadview.load_mesh(mesh_path)
    mesh = scene.to_geometry()
    mesh.merge_vertices()  # exporters (glTF especially) repeat vertices per face, which would split every face off
    if mesh_path.suffix.lower() in (".glb", ".gltf"):
        names, pieces = (list(t) for t in zip(*cadview.objects(scene), strict=True))
    else:
        pieces = mesh.split(only_watertight=False, repair=False) or [mesh]
        names = [f"body {i + 1} of {len(pieces)}" for i in range(len(pieces))]
    distances = [float(np.min(np.linalg.norm(p.vertices - np.asarray(point), axis=1))) for p in pieces]
    index = int(np.argmin(distances))
    piece = pieces[index]
    _, _, tri = trimesh.proximity.closest_point(mesh, [point])
    face_normal = mesh.face_normals[int(tri[0])].tolist() if len(tri) else None
    lo, hi = piece.bounds.tolist()
    size = [round(b - a, 2) for a, b in zip(lo, hi, strict=True)]
    details = {"object": names[index], "objects": len(pieces), "bbox": [vec(lo), vec(hi)], "size": size,
               "surface_normal": [round(c, 4) for c in face_normal] if face_normal else None}  # fmt: skip
    label = f"Point on {names[index]} (size {size[0]} × {size[1]} × {size[2]} mm) at {fmt(list(point))}"
    if face_normal:
        label += f", surface normal {fmt([round(c, 2) for c in face_normal])}"
    return SelectionItem(point=point, normal=normal, kind="point", label=label, details=details)
