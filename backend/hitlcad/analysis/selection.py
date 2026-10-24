"""Describe what the human clicked in a 3D view: a B-rep face, edge, vertex or whole part (STEP), or a
point on a mesh's named object or body, in mm, Z up."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hitlcad.analysis import cadview
from hitlcad.analysis.geometry import DESCRIBE, fmt, vec
from hitlcad.core.types import ComponentRef, SelectionItem, Vec


def describe_component(step: Path, ref: ComponentRef, point: Vec, normal: Vec | None = None) -> SelectionItem:
    """Describe exactly the face, edge, vertex or part the 3D view picked at `point`. The label names it
    as the geometry tools take it: "face 9 of model", "part lid"."""
    item = cadview.component(cadview.load_step(step), step.stem, ref)
    part = ref.part.rsplit("/", 1)[-1]
    details: dict[str, Any] = {"part": ref.part} if ref.topo == "solid" else {"part": ref.part, ref.topo: ref.index}
    label = DESCRIBE[ref.topo](item, details)
    name = f"part {part}" if ref.topo == "solid" else f"{ref.topo} {ref.index} of {part}"
    label = f"{label} ({name}) at {fmt(list(point))}"
    return SelectionItem(point=point, normal=normal, kind=ref.topo, label=label, details=details, component=ref)


def describe_mesh(mesh_path: Path, point: Vec, normal: Vec | None = None) -> SelectionItem:
    """A point on a mesh, on the object it hit when the file names its objects (glTF: Blender builds,
    loose .glb files), else on the connected body it hit."""
    import numpy as np
    import trimesh

    scene = cadview.load_mesh(mesh_path)
    mesh = scene.to_geometry()
    mesh.merge_vertices()  # exporters (glTF especially) repeat vertices per face, which would split every face off
    if mesh_path.suffix.lower() in (".glb", ".gltf"):
        names = list(scene.graph.nodes_geometry)
        pieces = [scene.geometry[scene.graph[n][1]].copy().apply_transform(scene.graph[n][0]) for n in names]
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


def describe_pick(path: Path, point: Vec, normal: Vec | None, component: ComponentRef | None) -> SelectionItem:
    """A click in the 3D view of `path`: STEP picks name their exact component, mesh picks are a point."""
    if path.suffix.lower() in cadview.BREP:
        if component is None:
            raise ValueError("a STEP pick must name its face, edge, vertex or part")
        return describe_component(path, component, point, normal)
    if component is not None:
        raise ValueError("meshes have no faces, edges, vertices or parts to pick")
    return describe_mesh(path, point, normal)
