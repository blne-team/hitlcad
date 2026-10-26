"""The 3D view (three-cad-viewer's instanced format, mm, Z up), and model files as parts.

A build's view (`build.assemble.write_view`) and a loose model file's (the file viewer) are written the
same way: each part tessellated once through its `Features` and drawn per instance at its place. A
B-rep part is one shape whose `faces_i`, `edges_i` and `vertices_i` are `faces()[i]`, `edges()[i]` and
`vertices()[i]` of the part, so a pick maps straight back to the model; a mesh part is one shape per
colour, each one face with its crease and boundary lines as edges. A loose model file opens as parts the
way a build's are: a STEP file's parts with their B-rep, a mesh file as one part of named objects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from hitlcad.core import frames as fr

if TYPE_CHECKING:
    import trimesh
    from build123d import Shape

    from hitlcad.geometry.features import Features

VIEWABLE = {".glb", ".gltf", ".stl", ".obj", ".ply", ".3mf", ".step", ".stp"}
BREP = {".step", ".stp"}
CREASE_DEG = 30.0  # mesh edges sharper than this are drawn as lines and shaded flat
MESH_COLOR = "#e8b024"  # ocp-tessellate's default part colour, so meshes and STEP files look alike
OTHER_SURFACE, OTHER_CURVE = 10, 8  # GeomAbs ordinals the viewer shows as "Other"
TOPO_LISTS = {"face": "faces", "edge": "edges", "vertex": "vertices"}
IDENTITY = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


# ---------------------------------------------------------------------------------------------- files


def load_step(path: Path) -> Shape:
    from build123d import import_step

    return import_step(str(path))


def load_mesh(path: Path) -> trimesh.Scene:
    """Any mesh file as a scene in mm, Z up. glTF is metres, Y up by definition; the rest are taken as CAD units."""
    import trimesh

    scene = trimesh.load(path, force="scene")
    if path.suffix.lower() in (".glb", ".gltf"):
        y_up_to_z_up = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
        scene.apply_transform(trimesh.transformations.scale_matrix(1000.0) @ y_up_to_z_up)
    return scene


@dataclass(frozen=True)
class ObjectMesh:
    """One mesh of a named object, in one material and colour (sRGB hex; None when its file gives none)."""

    name: str
    material: str | None
    color: str | None
    mesh: trimesh.Trimesh


def object_meshes(scene: trimesh.Scene) -> list[ObjectMesh]:
    """A scene's meshes, placed, by object. glTF splits an object with several materials into one mesh per
    material, under a node named after the object."""
    import trimesh

    out = []
    for node in scene.graph.nodes_geometry:
        transform, geometry = scene.graph[node]
        mesh = scene.geometry[geometry]
        if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
            continue
        name = scene.graph.transforms.parents[node] if mesh.metadata.get("from_gltf_primitive") else node
        material = getattr(mesh.visual, "material", None)
        # Only its geometry, placed: its colour is read here, and a copy of its visual would copy the file's
        # textures once per primitive (a glTF has one per B-rep face).
        placed = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False).apply_transform(transform)
        out.append(ObjectMesh(str(name), getattr(material, "name", None), _color(mesh), placed))
    return out


def _color(mesh: trimesh.Trimesh) -> str | None:
    """A mesh's colour as sRGB hex: its glTF material's base colour (linear in glTF, and white when the file
    leaves it out), or the colour its faces or vertices carry (3MF, PLY); None when its file gives it none."""
    from trimesh.visual.color import linear_to_srgb
    from trimesh.visual.material import PBRMaterial

    material = getattr(mesh.visual, "material", None)
    if isinstance(material, PBRMaterial):
        factor = material.baseColorFactor
        rgb = np.ones(3) if factor is None else linear_to_srgb(factor[:3] / 255)
    elif mesh.visual.kind in ("face", "vertex"):
        rgb = mesh.visual.main_color[:3] / 255
    else:
        return None
    return "#" + "".join(f"{v:02x}" for v in np.round(np.asarray(rgb) * 255).astype(int))


def geometry(objects: list[ObjectMesh]) -> trimesh.Trimesh:
    """Objects' meshes as one triangle mesh, cleaned as the analyses' meshes are (`mesh.clean`): glTF repeats
    vertices where normals differ."""
    import trimesh

    from hitlcad.geometry.mesh import clean

    whole = trimesh.util.concatenate([trimesh.Trimesh(o.mesh.vertices, o.mesh.faces, process=False) for o in objects])
    return clean(trimesh.Trimesh(whole.vertices, whole.faces))


def open_model(path: Path, tolerance: float, angular_tolerance: float) -> list[tuple[str, Features]]:
    """A loose model file as its parts, placed, by viewer id: a STEP file's parts (e.g. '/asm/block'), meshed
    as a build meshes its parts (`Settings.mesh_tolerance_mm`, `mesh_angular_tolerance`), or a mesh file as one
    part of its objects."""
    from hitlcad.geometry.features import BrepFeatures, MeshFeatures

    if path.suffix.lower() in BREP:  # a STEP file names parts, not objects within them
        return [(view_id, BrepFeatures(view_id.rsplit("/", 1)[-1], shape,
                                       lambda s=shape: _tessellated(s, tolerance, angular_tolerance), list))
                for view_id, shape in _step_parts(load_step(path), path.stem).items()]  # fmt: skip
    objects = object_meshes(load_mesh(path))
    if not objects:
        raise ValueError("no triangle meshes in the file")
    return [(f"/{path.stem}/{path.stem}", MeshFeatures(path.stem, lambda: geometry(objects), lambda: objects))]


def _tessellated(shape: Shape, tolerance: float, angular_tolerance: float) -> trimesh.Trimesh:
    import trimesh

    vertices, triangles = shape.tessellate(tolerance, angular_tolerance)
    return trimesh.Trimesh([(v.X, v.Y, v.Z) for v in vertices], triangles)


def _step_parts(shape: Shape, name: str) -> dict[str, Shape]:
    """Each part of a STEP file, by its viewer id (e.g. '/asm/block'), placed in model space."""
    from build123d import Shape
    from ocp_tessellate.cad_objects import OcpGroup
    from ocp_tessellate.convert import to_ocpgroup

    group, instances = to_ocpgroup(shape, names=[name])
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


# ---------------------------------------------------------------------------------------------- the view


class View:
    """A 3D view being written: each part tessellated once and drawn per instance."""

    def __init__(self) -> None:
        self.shapes: list[dict[str, Any]] = []
        self._refs: dict[int, list[tuple[int, dict[str, Any]]]] = {}

    def part(self, features: Features, view_id: str, name: str, transform: fr.Frame) -> dict[str, Any]:
        """An instance of a part: one shape, or a group of one per colour, at `transform`."""
        if id(features) not in self._refs:
            self._refs[id(features)] = []
            for shape, template in features.view():
                self.shapes.append(shape)
                self._refs[id(features)].append((len(self.shapes) - 1, template))
        drawn = self._refs[id(features)]
        if len(drawn) > 1:
            leaves = [{**t, "id": f"{view_id}/{t['name']}", "shape": {"ref": ref}} for ref, t in drawn]
            return group(view_id, name, leaves, transform)
        ((ref, template),) = drawn
        # A shape's loc places it in its part (ocp-tessellate's B-rep shapes sit at their solids' placement);
        # the instance places the part.
        return {**template, "id": view_id, "name": name, "shape": {"ref": ref},
                "loc": _loc(transform @ _frame(template["loc"]))}  # fmt: skip

    def write(self, tree: dict[str, Any], bounds: np.ndarray, target: Path) -> None:
        from ocp_tessellate.utils import numpy_to_buffer_json

        (lo, hi) = bounds
        tree["bb"] = {
            f"{axis}{end}": float(v[i]) for end, v in (("min", lo), ("max", hi)) for i, axis in enumerate("xyz")
        }
        data = numpy_to_buffer_json({"instances": self.shapes, "shapes": tree})
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(target)


def group(view_id: str, name: str, parts: list[dict[str, Any]], transform: fr.Frame = fr.IDENTITY) -> dict[str, Any]:
    return {"version": 3, "parts": parts, "loc": _loc(transform), "name": name, "id": view_id, "normal_len": 0}


def leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    if "parts" in node:
        return [leaf for child in node["parts"] for leaf in leaves(child)]
    return [node]


def write_view(source: Path, target: Path, tolerance: float, angular_tolerance: float) -> None:
    """A loose model file's view, for the file viewer."""
    view, parts = View(), open_model(source, tolerance, angular_tolerance)
    tree = group(
        f"/{source.stem}", source.stem, [view.part(f, vid, vid.rsplit("/", 1)[-1], fr.IDENTITY) for vid, f in parts]
    )
    corners = np.vstack([f.bounds() for _, f in parts])
    view.write(tree, np.array([corners.min(axis=0), corners.max(axis=0)]), target)


def cached_view(source: Path, cache_dir: Path, tolerance: float, angular_tolerance: float) -> Path:
    """The view of a loose model file, cached by its content and how finely it is meshed."""
    key = hashlib.sha256(source.read_bytes() + f"{tolerance:g} {angular_tolerance:g}".encode()).hexdigest()
    target = cache_dir / f"{key[:24]}.json"
    if not target.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_view(source, target, tolerance, angular_tolerance)
    return target


def quaternion(m: fr.Frame) -> tuple[float, float, float, float]:
    """(x, y, z, w) of a rotation matrix, as the view takes it (trimesh's are (w, x, y, z))."""
    import trimesh

    w, x, y, z = trimesh.transformations.quaternion_from_matrix(m)
    return x, y, z, w


def _loc(m: fr.Frame) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    return (tuple(float(v) for v in m[:3, 3]), tuple(float(v) for v in quaternion(m)))  # type: ignore[return-value]


def _frame(loc: Any) -> fr.Frame:
    """The matrix of a view `loc`: a position and an (x, y, z, w) quaternion."""
    import trimesh

    position, (x, y, z, w) = loc
    m = trimesh.transformations.quaternion_matrix((w, x, y, z))
    m[:3, 3] = position
    return m


# ---------------------------------------------------------------------------------------------- mesh shapes


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


def mesh_entry(mesh: trimesh.Trimesh, name: str, color: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A mesh tessellated for the viewer, and its entry template."""
    return _mesh_shape(mesh), {
        "type": "shapes", "subtype": "solid", "name": name, "state": [1, 1], "color": color or MESH_COLOR,
        "alpha": 1.0, "material": None, "normalize_uvs": True, "texture": None, "loc": IDENTITY, "renderback": False,
        "accuracy": None, "bb": None,
    }  # fmt: skip
