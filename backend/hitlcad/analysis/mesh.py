"""Loading and measuring triangle meshes (mm, Z up)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import trimesh

from hitlcad.core.report import MeshMetrics

if TYPE_CHECKING:
    import manifold3d


def load_mesh(path: Path) -> trimesh.Trimesh:
    """A mesh file, its vertices merged and its zero-area triangles (a mesher's apexes and seams) dropped, as
    the solid it is (`solid`)."""
    mesh = trimesh.load(path, force="mesh", process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{path.name} did not load as a triangle mesh")
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices()
    return solid(mesh)


def solid(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Closed shells that overlap as the one solid they make: a part modelled as several objects (a Blender
    figure's head, eyes and spots) would otherwise measure their buried surfaces as walls and their shared
    volume twice. Shells facing inwards are hollows, cut from it; a shell enclosing nothing (a flattened
    sliver) is no material and is left out. Shells that do not touch stay apart."""
    import manifold3d

    shells = mesh.split(only_watertight=False)
    if len(shells) < 2 or not all(s.is_watertight for s in shells) or not _overlapping([s.bounds for s in shells]):
        return mesh
    volumes = [_signed_volume(s) for s in shells]

    def body(shell: trimesh.Trimesh, volume: float) -> manifold3d.Manifold:
        faces = shell.faces if volume > 0 else shell.faces[:, ::-1]
        return manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(shell.vertices, dtype=np.float32),
                                                   tri_verts=np.asarray(faces, dtype=np.uint32)))  # fmt: skip

    solids = [body(s, v) for s, v in zip(shells, volumes, strict=True) if v > 0]
    add = manifold3d.Manifold.batch_boolean(solids, manifold3d.OpType.Add)
    hollows = [body(s, v) for s, v in zip(shells, volumes, strict=True) if v < 0]
    whole = add - manifold3d.Manifold.batch_boolean(hollows, manifold3d.OpType.Add) if hollows else add
    out = whole.to_mesh()
    return trimesh.Trimesh(out.vert_properties[:, :3], out.tri_verts)


def _signed_volume(shell: trimesh.Trimesh) -> float:
    """A closed shell's volume, negative when it faces inwards: the sum of its triangles' signed tetrahedra."""
    a, b, c = shell.triangles[:, 0], shell.triangles[:, 1], shell.triangles[:, 2]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)


def _overlapping(bounds: list[np.ndarray]) -> bool:
    """Whether any two bounding boxes overlap."""
    return any(np.all(a[0] < b[1]) and np.all(b[0] < a[1]) for i, a in enumerate(bounds) for b in bounds[i + 1 :])


def closed(mesh: trimesh.Trimesh) -> manifold3d.Manifold | None:
    """The solid a mesh bounds, or None when it bounds none: manifold3d's test, the one every measurement of
    volume, mass, overlap and gap shares. It reads a mesh by its edges' pairing, so solids that touch along
    an edge (a bearing's balls on their races) are still closed."""
    import manifold3d

    if mesh.is_empty:
        return None
    solid = manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
                                                tri_verts=np.asarray(mesh.faces, dtype=np.uint32)))  # fmt: skip
    return None if solid.status() != manifold3d.Error.NoError or solid.is_empty() else solid


def measure(mesh: trimesh.Trimesh, solid: manifold3d.Manifold | None) -> MeshMetrics:
    """A part's mesh, and the solid it bounds (`closed`)."""
    if mesh.is_empty:
        zero = (0.0, 0.0, 0.0)
        return MeshMetrics(bbox_min=zero, bbox_max=zero, size=zero, volume=None, surface_area=0.0,
                           triangles=0, bodies=0, watertight=False, winding_consistent=False)  # fmt: skip
    lo, hi = (tuple(round(float(v), 4) for v in row) for row in mesh.bounds)
    return MeshMetrics(
        bbox_min=lo,  # type: ignore[arg-type]
        bbox_max=hi,  # type: ignore[arg-type]
        size=_size(mesh.bounds),
        volume=round(float(solid.volume()), 4) if solid else None,
        surface_area=round(float(mesh.area), 4),
        triangles=len(mesh.faces),
        bodies=len(mesh.split(only_watertight=False)),
        watertight=solid is not None,
        winding_consistent=bool(mesh.is_winding_consistent),
    )


def measure_union(bounds: np.ndarray, parts: list[MeshMetrics]) -> MeshMetrics:
    """Placed parts as one, within `bounds`: closed when each is, the material and surface the parts' added
    up. Measured from the parts, not from one mesh of them all: parts that touch would share faces in it,
    which on its own would read as open, and the whole would be measured again for nothing."""
    closed = all(p.watertight for p in parts)
    return MeshMetrics(
        bbox_min=tuple(round(float(v), 4) for v in bounds[0]),  # type: ignore[arg-type]
        bbox_max=tuple(round(float(v), 4) for v in bounds[1]),  # type: ignore[arg-type]
        size=_size(bounds),
        volume=round(sum(p.volume or 0.0 for p in parts), 4) if closed else None,
        surface_area=round(sum(p.surface_area for p in parts), 4),
        triangles=sum(p.triangles for p in parts),
        bodies=sum(p.bodies for p in parts),
        watertight=closed,
        winding_consistent=all(p.winding_consistent for p in parts),
    )


def _size(bounds: np.ndarray) -> tuple[float, float, float]:
    return tuple(round(float(v), 4) for v in bounds[1] - bounds[0])  # type: ignore[return-value]
