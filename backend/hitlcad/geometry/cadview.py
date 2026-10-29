"""The 3D view (three-cad-viewer's instanced format, mm, Z up), and model files as parts.

A part is tessellated for the view once, into a `PartView` file in its own frame, and drawn per instance at
its place. A view is a `Scene`: the tree of instances, each naming its part's view file, which the panel
fetches once by its digest and reads as it lies. A built part's view file is written by its backend beside
its other files and kept with them in the part cache, so a build's scene (`build.assemble.write_scene`) only
places them; a loose model file's (the file viewer) is tessellated from its `Features` when it opens. A B-rep
part is one shape whose `faces_i`, `edges_i` and `vertices_i` are `faces()[i]`, `edges()[i]` and
`vertices()[i]` of the part, so a pick maps straight back to the model; a mesh part is one shape per colour,
each one face with its crease and boundary lines as edges. A loose model file opens as parts the way a
build's are: a STEP file's parts with their B-rep, a mesh file as one part of named objects.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import struct
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from hitlcad.core import frames as fr

if TYPE_CHECKING:
    import trimesh
    from build123d import Location, Shape

    from hitlcad.geometry.features import Features

VIEWABLE = {".glb", ".gltf", ".stl", ".obj", ".ply", ".3mf", ".step", ".stp"}
BREP = {".step", ".stp"}
CREASE_DEG = 30.0  # mesh edges sharper than this are drawn as lines and shaded flat
MESH_COLOR = "#e8b024"  # a part's colour where its file gives none, B-rep or mesh alike
VIEW_DEVIATION = 0.001  # the view's chordal deviation, of a B-rep's mean size
VIEW_ANGULAR = 0.2  # the view's angular deviation, rad
OTHER_SURFACE, OTHER_CURVE = 10, 8  # GeomAbs ordinals the viewer shows as "Other"
TOPO_LISTS = {"face": "faces", "edge": "edges", "vertex": "vertices"}
IDENTITY = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
SCENE_FILE = "scene.json"
VIEW_SUFFIX = ".view"


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


@dataclass(frozen=True)
class Piece:
    """What of a part has one colour: one shape in the view, one object in 3MF."""

    name: str
    color: str | None
    mesh: trimesh.Trimesh


def pieces(name: str, objects: list[ObjectMesh]) -> list[Piece]:
    """A part's objects by colour, each piece named by its materials (by the part, where they name none)."""
    by_color: dict[str | None, list[ObjectMesh]] = {}
    for o in objects:
        by_color.setdefault(o.color, []).append(o)
    named = {color: "+".join(dict.fromkeys(o.material for o in meshes if o.material))
             for color, meshes in by_color.items()}  # fmt: skip
    return [Piece(named[color] or name, color, geometry(meshes)) for color, meshes in by_color.items()]


def flat(shape: Shape) -> Shape:
    """A shape as one object: a compound of its solids with no children, so viewers and face numbering
    treat it as one part."""
    from build123d import Compound

    solids = shape.solids()
    return Compound(list(solids)) if len(solids) != 1 else solids[0]


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
    """Each part of a STEP file, placed in model space, by its viewer id: the file's name, then its labels down to
    the part (e.g. '/asm/block'); of siblings labelled alike, the second is 'block(2)', and so on."""
    found: dict[str, Shape] = {}

    def walk(node: Shape, path: str, placed: Location | None) -> None:
        children = list(node.children)
        if not children:
            found[path] = node if placed is None else node.moved(placed)
            return
        here = node.location if placed is None else placed * node.location
        seen: dict[str, int] = {}
        for child in children:
            label = child.label or type(child).__name__
            seen[label] = n = seen.get(label, 0) + 1
            walk(child, f"{path}/{label}" + (f"({n})" if n > 1 else ""), here)

    walk(shape, f"/{name}" + ("" if shape.children else f"/{shape.label or name}"), None)
    return found


# ---------------------------------------------------------------------------------------------- the view


@dataclass(frozen=True)
class PartView:
    """A part tessellated for the view, in its own frame: each shape the view draws of it (its arrays) with its
    entry template, whose `loc` places the shape in the part, and the box around them all.

    On disk (`parts/<part>.view`, beside the part's other files) it is what the panel reads without decoding: a
    little-endian u32, that many bytes of JSON header (the bounds, the templates and where each array lies), then
    the arrays, float32 or uint32, at 4-byte offsets from the end of the header."""

    shapes: list[dict[str, np.ndarray]]
    templates: list[dict[str, Any]]
    bounds: np.ndarray

    @classmethod
    def of(cls, drawn: list[tuple[dict[str, np.ndarray], dict[str, Any]]]) -> PartView:
        placed = np.vstack([fr.transform_points(np.reshape(s["vertices"], (-1, 3)), _frame(t["loc"]))
                            for s, t in drawn])  # fmt: skip
        return cls([s for s, _ in drawn], [t for _, t in drawn], np.array([placed.min(axis=0), placed.max(axis=0)]))

    def write(self, target: Path) -> None:
        arrays, body, offset = [], [], 0
        for shape in self.shapes:
            placed = {}
            for key, value in shape.items():
                a = np.ascontiguousarray(value, dtype="<f4" if np.asarray(value).dtype.kind == "f" else "<u4").ravel()
                placed[key] = {"dtype": "float32" if a.dtype.kind == "f" else "uint32", "offset": offset,
                               "length": len(a)}  # fmt: skip
                body.append(a.tobytes())
                offset += a.nbytes
            arrays.append(placed)
        header = json.dumps({"bounds": self.bounds.tolist(), "templates": self.templates, "arrays": arrays},
                            separators=(",", ":")).encode()  # fmt: skip
        header += b" " * (-len(header) % 4)
        target.write_bytes(struct.pack("<I", len(header)) + header + b"".join(body))

    @staticmethod
    def header(data: bytes) -> dict[str, Any]:
        """The header of a view file's bytes: its bounds, templates and arrays' places."""
        (size,) = struct.unpack_from("<I", data)
        return json.loads(data[4 : 4 + size])  # type: ignore[no-any-return]

    @classmethod
    def read(cls, path: Path) -> PartView:
        data = path.read_bytes()
        head = cls.header(data)
        start = 4 + struct.unpack_from("<I", data)[0]
        shapes = [{key: np.frombuffer(data, dtype=f"<{'f' if a['dtype'] == 'float32' else 'u'}4", count=a["length"],
                                      offset=start + a["offset"]) for key, a in arrays.items()}
                  for arrays in head["arrays"]]  # fmt: skip
        return cls(shapes, head["templates"], np.array(head["bounds"]))


def brep_view(shape: Shape, name: str, objects: list[ObjectMesh]) -> PartView:
    """One shape with every face, edge and vertex as picks number them (`flat`), marked `exact` so the panel's
    clicks name them. A viewer shape has one colour: a part made in several shows the one most of its objects'
    surface has."""
    area: defaultdict[str, float] = defaultdict(float)
    for o in objects:
        if o.color:
            area[o.color] += float(o.mesh.area)
    color = max(area, key=area.__getitem__) if area else None
    return PartView.of([(_brep_shape(flat(shape)), {**_template(name, color), "exact": True})])


def _brep_shape(shape: Shape) -> dict[str, np.ndarray]:
    """A B-rep meshed for the view: each of `faces()` its triangles and normals, each of `edges()` its line
    segments, each of `vertices()` its point, all in the order picks number them, a face or edge with nothing to
    draw (a degenerate edge, a face the mesher could not mesh) included, empty. Meshed to a deviation of
    `VIEW_DEVIATION` of the shape's mean size and `VIEW_ANGULAR` radians, in parallel."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_Orientation
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location

    size = shape.bounding_box().size
    deflection = max((size.X + size.Y + size.Z) / 3 * VIEW_DEVIATION, 1e-6)
    BRepMesh_IncrementalMesh(shape.wrapped, deflection, False, VIEW_ANGULAR, True)

    vertices: list[np.ndarray] = []
    normals: list[np.ndarray] = []
    triangles: list[np.ndarray] = []
    per_face, face_types, mesh_of = [], [], {}
    offset = 0
    point, normal = gp_Pnt(), gp_Vec()
    for face in shape.faces():
        topo = face.wrapped
        face_types.append(int(BRepAdaptor_Surface(topo).GetType().value))
        loc = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(topo, loc)
        explorer = TopExp_Explorer(topo, TopAbs_EDGE)
        while explorer.More():
            mesh_of.setdefault(hash(explorer.Current()), (topo, poly, loc))
            explorer.Next()
        if poly is None:
            per_face.append(0)
            continue
        moved = loc.Transformation()
        nodes = np.array([poly.Node(i).Transformed(moved).Coord() for i in range(1, poly.NbNodes() + 1)])
        corners = np.array([poly.Triangle(i).Get() for i in range(1, poly.NbTriangles() + 1)]) - 1
        if topo.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            corners = corners[:, [0, 2, 1]]
        if poly.HasUVNodes():
            prop, (u0, u1, v0, v1) = BRepGProp_Face(topo), BRepTools.UVBounds_s(topo)
            at = []
            for i in range(1, poly.NbNodes() + 1):
                u, v = poly.UVNode(i).Coord()
                # A node's UV may lie a hair outside the face at a singularity (a sphere's pole), where the normal
                # turns inward: evaluate it inside.
                prop.Normal(min(max(u, u0), u1), min(max(v, v0), v1), point, normal)
                at.append(normal.Coord())
            unit = np.array(at)
            unit /= np.maximum(np.linalg.norm(unit, axis=1, keepdims=True), 1e-12)
            if topo.Orientation() == TopAbs_Orientation.TopAbs_INTERNAL:
                unit = -unit
        else:
            unit = np.zeros_like(nodes)
            np.add.at(unit, corners, np.cross(nodes[corners[:, 1]] - nodes[corners[:, 0]],
                                              nodes[corners[:, 2]] - nodes[corners[:, 0]])[:, None])  # fmt: skip
            unit /= np.maximum(np.linalg.norm(unit, axis=1, keepdims=True), 1e-12)
        vertices.append(nodes)
        normals.append(unit)
        triangles.append(corners + offset)
        per_face.append(len(corners))
        offset += len(nodes)

    segments: list[np.ndarray] = []
    per_edge, edge_types = [], []
    for edge in shape.edges():
        topo = edge.wrapped
        edge_types.append(int(BRepAdaptor_Curve(topo).GetType().value))
        line = None
        if (meshed := mesh_of.get(hash(topo))) and meshed[1] is not None:
            on = BRep_Tool.PolygonOnTriangulation_s(topo, meshed[1], meshed[2])
            if on is not None:
                moved = meshed[2].Transformation()
                line = np.array(
                    [meshed[1].Node(on.Node(j)).Transformed(moved).Coord() for j in range(1, on.NbNodes() + 1)]
                )
        if line is None and not BRep_Tool.Degenerated_s(topo):  # an edge of no face: along its curve
            curve = BRepAdaptor_Curve(topo)
            points = GCPnts_QuasiUniformDeflection(curve, deflection)
            line = (
                np.array([points.Value(j).Coord() for j in range(1, points.NbPoints() + 1)])
                if points.IsDone()
                else None
            )
        if line is None or len(line) < 2:
            per_edge.append(0)
            continue
        segments.append(np.stack([line[:-1], line[1:]], axis=1).reshape(-1, 3))
        per_edge.append(len(line) - 1)

    def joined(parts: list[np.ndarray], width: int) -> np.ndarray:
        return np.vstack(parts) if parts else np.zeros((0, width))

    return {
        "vertices": joined(vertices, 3).astype(np.float32).ravel(),
        "triangles": joined(triangles, 3).astype(np.uint32).ravel(),
        "normals": joined(normals, 3).astype(np.float32).ravel(),
        "edges": joined(segments, 3).astype(np.float32),
        "obj_vertices": np.array([(v.X, v.Y, v.Z) for v in shape.vertices()], dtype=np.float32).ravel(),
        "face_types": np.array(face_types, dtype=np.uint32),
        "edge_types": np.array(edge_types, dtype=np.uint32),
        "triangles_per_face": np.array(per_face, dtype=np.uint32),
        "segments_per_edge": np.array(per_edge, dtype=np.uint32),
    }


def _template(name: str, color: str | None) -> dict[str, Any]:
    """A shape's entry in the view, before its instance names and places it."""
    return {"type": "shapes", "subtype": "solid", "name": name, "state": [1, 1], "color": color or MESH_COLOR,
            "alpha": 1.0, "material": None, "normalize_uvs": True, "texture": None, "loc": IDENTITY,
            "renderback": False, "accuracy": None, "bb": None}  # fmt: skip


def mesh_view(parts: list[Piece]) -> PartView:
    """One shape per colour (`pieces`), each with its crease and boundary lines as edges."""
    return PartView.of([mesh_entry(p.mesh, p.name.replace("/", "_"), p.color) for p in parts])


class Scene:
    """A 3D view being written into `root`: the tree of instances, each drawing its part's view file (by its path
    under `root` and its digest, which the panel fetches once whatever the revision), and the box around them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.instances: list[dict[str, Any]] = []
        self._parts: dict[Path, tuple[list[tuple[int, dict[str, Any]]], np.ndarray]] = {}
        self._corners: list[np.ndarray] = []

    def part(self, view: Path, view_id: str, name: str, transform: fr.Frame) -> dict[str, Any]:
        """An instance of the part whose view file is `view`: one shape, or a group of one per colour, at
        `transform`."""
        if view not in self._parts:
            data = view.read_bytes()
            head, digest = PartView.header(data), hashlib.sha256(data).hexdigest()[:32]
            path = view.relative_to(self.root).as_posix()
            refs = []
            for i, template in enumerate(head["templates"]):
                self.instances.append({"path": path, "digest": digest, "shape": i})
                refs.append((len(self.instances) - 1, template))
            self._parts[view] = refs, np.array(head["bounds"])
        drawn, bounds = self._parts[view]
        self._corners.append(fr.transform_points(np.array(list(itertools.product(*bounds.T))), transform))
        if len(drawn) > 1:
            parts = [{**t, "id": f"{view_id}/{t['name']}", "shape": {"ref": ref}} for ref, t in drawn]
            return group(view_id, name, parts, transform)
        ((ref, template),) = drawn
        # A shape's loc places it in its part; the instance places the part.
        return {**template, "id": view_id, "name": name, "shape": {"ref": ref},
                "loc": _loc(transform @ _frame(template["loc"]))}  # fmt: skip

    def write(self, tree: dict[str, Any], name: str = SCENE_FILE) -> None:
        corners = np.vstack(self._corners) if self._corners else np.zeros((1, 3))  # nothing placed: an empty scene
        lo, hi = corners.min(axis=0), corners.max(axis=0)
        tree["bb"] = {
            f"{axis}{end}": float(v[i]) for end, v in (("min", lo), ("max", hi)) for i, axis in enumerate("xyz")
        }
        target = self.root / name
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"instances": self.instances, "shapes": tree}, separators=(",", ":")),
                       encoding="utf-8")  # fmt: skip
        tmp.replace(target)


def group(view_id: str, name: str, parts: list[dict[str, Any]], transform: fr.Frame = fr.IDENTITY) -> dict[str, Any]:
    return {"version": 3, "parts": parts, "loc": _loc(transform), "name": name, "id": view_id, "normal_len": 0}


def leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    if "parts" in node:
        return [leaf for child in node["parts"] for leaf in leaves(child)]
    return [node]


def write_scene(source: Path, root: Path, tolerance: float, angular_tolerance: float) -> None:
    """A loose model file's scene, for the file viewer: each of its parts' view as `<n>.view` in `root`."""
    scene, placed = Scene(root), []
    for n, (view_id, features) in enumerate(open_model(source, tolerance, angular_tolerance)):
        features.view().write(root / f"{n}{VIEW_SUFFIX}")
        placed.append(scene.part(root / f"{n}{VIEW_SUFFIX}", view_id, view_id.rsplit("/", 1)[-1], fr.IDENTITY))
    scene.write(group(f"/{source.stem}", source.stem, placed))


def cached_scene(source: Path, cache_dir: Path, tolerance: float, angular_tolerance: float) -> str:
    """The key of a loose model file's scene, a directory of `cache_dir` holding `scene.json` and its parts' views,
    cached by the file's content and how finely it is meshed. Each request writes its own, and the first to finish
    is kept: a scene being read is never replaced."""
    key = hashlib.sha256(source.read_bytes() + f"{tolerance:g} {angular_tolerance:g}".encode()).hexdigest()[:24]
    root = cache_dir / key
    if not (root / SCENE_FILE).is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{key}.", dir=cache_dir))
        try:
            write_scene(source, staging, tolerance, angular_tolerance)
            staging.rename(root)
        except OSError:
            if not (root / SCENE_FILE).is_file():  # not another request's, finished first
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return key


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
    segments = segments[segments[:, 0] != segments[:, 1]]  # a degenerate triangle's edge draws nothing, and is no run
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
    return _mesh_shape(mesh), _template(name, color)
