"""Loading and measuring triangle meshes (mm, Z up)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from hitlcad.core.report import MeshMetrics


def load_mesh(path: Path) -> trimesh.Trimesh:
    """A mesh file, its vertices merged and its zero-area triangles (a mesher's apexes and seams) dropped."""
    mesh = trimesh.load(path, force="mesh", process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{path.name} did not load as a triangle mesh")
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices()
    return mesh


def measure(mesh: trimesh.Trimesh) -> MeshMetrics:
    if mesh.is_empty:
        zero = (0.0, 0.0, 0.0)
        return MeshMetrics(bbox_min=zero, bbox_max=zero, size=zero, volume=None, surface_area=0.0,
                           triangles=0, bodies=0, watertight=False, winding_consistent=False)  # fmt: skip
    lo, hi = (tuple(round(float(v), 4) for v in row) for row in mesh.bounds)
    size = tuple(round(float(v), 4) for v in np.asarray(hi) - np.asarray(lo))
    watertight = bool(mesh.is_watertight)
    return MeshMetrics(
        bbox_min=lo,  # type: ignore[arg-type]
        bbox_max=hi,  # type: ignore[arg-type]
        size=size,  # type: ignore[arg-type]
        volume=round(float(abs(mesh.volume)), 4) if watertight else None,
        surface_area=round(float(mesh.area), 4),
        triangles=len(mesh.faces),
        bodies=len(mesh.split(only_watertight=False)),
        watertight=watertight,
        winding_consistent=bool(mesh.is_winding_consistent),
    )


def measure_union(whole: MeshMetrics, parts: list[MeshMetrics]) -> MeshMetrics:
    """Several parts as one: closed when each is, the material the parts' added up. Parts that touch share
    faces in the combined mesh, which on its own would read as open."""
    closed = all(p.watertight for p in parts)
    return whole.model_copy(update={
        "watertight": closed,
        "winding_consistent": all(p.winding_consistent for p in parts),
        "volume": round(sum(p.volume or 0.0 for p in parts), 4) if closed else None,
        "bodies": sum(p.bodies for p in parts),
    })  # fmt: skip
