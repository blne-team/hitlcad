"""Loose model files as three-cad-viewer input (its instanced format), in mm, Z up: the file viewer. A
build's own view is written by `build.assemble` from its parts.

STEP keeps its B-rep topology: part `/asm/block` carries `faces_i`, `edges_i` and `vertices_i`, which are
`faces()[i]`, `edges()[i]` and `vertices()[i]` of that part in build123d, so `component` maps a pick in
the viewer straight back to the model. Meshes become one part per mesh with a single face, and their
crease and boundary lines as edges.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import trimesh
    from build123d import Shape

    from hitlcad.core.types import ComponentRef

VIEWABLE = {".glb", ".gltf", ".stl", ".obj", ".ply", ".3mf", ".step", ".stp"}
BREP = {".step", ".stp"}
CREASE_DEG = 30.0  # mesh edges sharper than this are drawn as lines and shaded flat
MESH_COLOR = "#e8b024"  # ocp-tessellate's default part colour, so meshes and STEP files look alike
OTHER_SURFACE, OTHER_CURVE = 10, 8  # GeomAbs ordinals the viewer shows as "Other"
TOPO_LISTS = {"face": "faces", "edge": "edges", "vertex": "vertices"}


def write_view(source: Path, target: Path) -> None:
    """Tessellate a loose model file (STEP or any mesh) into `target` for the file viewer."""
    data = (
        brep_view(load_step(source), source.stem)
        if source.suffix.lower() in BREP
        else mesh_view(load_mesh(source), source.stem)
    )
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(target)


def cached_view(source: Path, cache_dir: Path) -> Path:
    """The view of a loose model file, cached by its content."""
    target = cache_dir / f"{hashlib.sha256(source.read_bytes()).hexdigest()[:24]}.json"
    if not target.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_view(source, target)
    return target


def load_step(path: Path) -> Shape:
    from build123d import import_step

    return import_step(str(path))


def load_mesh(path: Path) -> trimesh.Scene:
    """Any mesh file as a scene in mm, Z up. glTF is metres, Y up by definition; the rest are taken as CAD units."""
    import numpy as np
    import trimesh

    scene = trimesh.load(path, force="scene")
    if path.suffix.lower() in (".glb", ".gltf"):
        y_up_to_z_up = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
        scene.apply_transform(trimesh.transformations.scale_matrix(1000.0) @ y_up_to_z_up)
    return scene


# ---------------------------------------------------------------------------------------------- B-rep


def _group(shape: Shape, name: str) -> tuple[Any, list[dict[str, Any]]]:
    from ocp_tessellate.convert import to_ocpgroup

    return to_ocpgroup(shape, names=[name])


def brep_view(shape: Shape, name: str) -> dict[str, Any]:
    from ocp_tessellate.convert import tessellate_group
    from ocp_tessellate.utils import numpy_to_buffer_json

    group, instances = _group(shape, name)
    meshed, shapes, _ = tessellate_group(group, instances)
    return numpy_to_buffer_json({"instances": meshed, "shapes": shapes})


def parts(shape: Shape, name: str) -> dict[str, Shape]:
    """Each part the viewer shows, by its path (e.g. '/asm/block'), placed in model space."""
    from build123d import Shape
    from ocp_tessellate.cad_objects import OcpGroup

    group, instances = _group(shape, name)
    found: dict[str, Shape] = {}

    def walk(node: Any, path: str, loc: Any) -> None:
        path = f"{path}/{node.name}"
        if node.loc is not None:
            loc = node.loc if loc is None else loc.Multiplied(node.loc)
        if isinstance(node, OcpGroup):
            for child in node.objects:
                walk(child, path, loc)
        elif node.ref is not None:
            solid = instances[node.ref]["obj"]
            found[path] = Shape.cast(solid if loc is None else solid.Moved(loc))

    walk(group, "", None)
    return found


def component(shape: Shape, name: str, ref: ComponentRef) -> Shape:
    """The build123d face, edge, vertex or whole part a viewer pick refers to, placed in model space."""
    part = parts(shape, name).get(ref.part)
    if part is None:
        raise LookupError(f"no part {ref.part}")
    if ref.topo == "solid":
        return part
    items = getattr(part, TOPO_LISTS[ref.topo])()
    if ref.index >= len(items):
        raise LookupError(f"{ref.part} has {len(items)} {ref.topo}s, not {ref.index + 1}")
    return items[ref.index]


# ---------------------------------------------------------------------------------------------- meshes


def _meshes(scene: trimesh.Scene) -> Iterator[tuple[str, trimesh.Trimesh]]:
    import trimesh

    for node in scene.graph.nodes_geometry:
        transform, geometry = scene.graph[node]
        mesh = scene.geometry[geometry]
        if isinstance(mesh, trimesh.Trimesh) and len(mesh.faces):
            yield str(node).replace("/", "_"), mesh.copy().apply_transform(transform)


def _mesh_shape(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """One face (the whole mesh) and one edge per connected run of crease or boundary lines."""
    import numpy as np
    import trimesh

    sharp = np.radians(CREASE_DEG)
    shaded = trimesh.graph.smooth_shade(mesh, angle=sharp)
    crease = mesh.face_adjacency_edges[mesh.face_adjacency_angles > sharp]
    boundary = mesh.edges_sorted[trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)]
    segments = np.vstack([crease, boundary]).astype(np.int64).reshape(-1, 2)
    runs = trimesh.graph.connected_components(segments, min_len=2) if len(segments) else []
    run_of = np.full(len(mesh.vertices), -1)
    for i, run in enumerate(runs):
        run_of[run] = i
    order = np.argsort(run_of[segments[:, 0]], kind="stable")
    segments = segments[order]
    counts = np.bincount(run_of[segments[:, 0]], minlength=len(runs)) if len(segments) else np.zeros(0)
    return {
        "vertices": np.asarray(shaded.vertices, dtype=np.float32),
        "triangles": np.asarray(shaded.faces, dtype=np.int32),
        "normals": np.asarray(shaded.vertex_normals, dtype=np.float32),
        "edges": np.asarray(mesh.vertices[segments], dtype=np.float32).reshape(-1, 3),
        "obj_vertices": np.zeros((0, 3), dtype=np.float32),
        "face_types": np.array([OTHER_SURFACE], dtype=np.int32),
        "edge_types": np.full(len(runs), OTHER_CURVE, dtype=np.int32),
        "triangles_per_face": np.array([len(shaded.faces)], dtype=np.int32),
        "segments_per_edge": np.asarray(counts, dtype=np.int32),
    }


IDENTITY = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mesh_view_parts(scene: trimesh.Scene, name: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each mesh of a scene tessellated for the viewer, and its entry in the view's tree."""
    instances, parts = [], []
    for i, (node, mesh) in enumerate(_meshes(scene)):
        instances.append(_mesh_shape(mesh))
        parts.append({
            "id": f"/{name}/{node}", "type": "shapes", "subtype": "solid", "name": node, "shape": {"ref": i},
            "state": [1, 1], "color": MESH_COLOR, "alpha": 1.0, "material": None, "normalize_uvs": True,
            "texture": None, "loc": IDENTITY, "renderback": False, "accuracy": None, "bb": None,
        })  # fmt: skip
    return instances, parts


def mesh_view(scene: trimesh.Scene, name: str) -> dict[str, Any]:
    from ocp_tessellate.utils import numpy_to_buffer_json

    instances, parts = mesh_view_parts(scene, name)
    if not parts:
        raise ValueError("no triangle meshes in the file")
    lo, hi = scene.bounds
    bb = {f"{axis}{end}": float(v[i]) for end, v in (("min", lo), ("max", hi)) for i, axis in enumerate("xyz")}
    shapes = {"version": 3, "loc": IDENTITY, "name": name, "id": f"/{name}", "normal_len": 0, "bb": bb, "parts": parts}
    return numpy_to_buffer_json({"instances": instances, "shapes": shapes})
