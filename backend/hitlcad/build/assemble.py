"""What a built design becomes on disk: the assembly as a STEP product structure, as one mesh, and as the
3D view (three-cad-viewer's instanced format).

The view tessellates each part once and draws it per instance at the instance's place, nested by
component. Viewer ids are `/<design>/<instance path>`, and "face i" of an instance is `faces()[i]` of
its part's STEP (`parts/<part>.step`), the way picks and the geometry tools number them.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from hitlcad.analysis import cadview
from hitlcad.analysis.geometry import location
from hitlcad.analysis.model import BuiltDesign, PartGeometry
from hitlcad.core import frames as fr

IDENTITY_LOC = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def flat(shape: Any) -> Any:
    """A part's shape as one object: a compound of its solids with no children, so viewers and face
    numbering treat it as one part."""
    from build123d import Compound

    solids = shape.solids()
    return Compound(list(solids)) if len(solids) != 1 else solids[0]


def step_assembly(built: BuiltDesign, target: Path) -> bool:
    """The assembly as STEP: nested compounds named by instance, each part shared by its instances. False
    (and nothing written) when a part has no B-rep."""
    from build123d import Compound, export_step

    if any(part.step_file is None for part in _all_parts(built)):
        return False
    shapes: dict[int, Any] = {}

    def node(design: BuiltDesign, name: str) -> Any:
        children = []
        for address, part_or_component, local in design.instances():
            if part_or_component in design.components:
                child = node(design.components[part_or_component], address)
                child.location = location(local)
            else:
                part = design.parts[part_or_component]
                base = shapes.setdefault(id(part), flat(part.shape))
                child = base.moved(location(local))
                child.label = address
            children.append(child)
        return Compound(children=children, label=name)

    export_step(node(built, built.spec.name), str(target))
    return True


def _all_parts(built: BuiltDesign) -> list[PartGeometry]:
    return [*built.parts.values(), *(p for c in built.components.values() for p in _all_parts(c))]


def quaternion(m: fr.Frame) -> tuple[float, float, float, float]:
    """(x, y, z, w) of a rotation matrix, by Shepperd's method: stable for every rotation, half turns included."""
    r = m[:3, :3]
    trace = r[0, 0] + r[1, 1] + r[2, 2]
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        return ((r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, s / 4)
    if r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        return (s / 4, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s)
    if r[1, 1] > r[2, 2]:
        s = math.sqrt(1 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        return ((r[0, 1] + r[1, 0]) / s, s / 4, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s)
    s = math.sqrt(1 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
    return ((r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, s / 4, (r[1, 0] - r[0, 1]) / s)


def _loc(m: fr.Frame) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    return (tuple(float(v) for v in m[:3, 3]), tuple(float(v) for v in quaternion(m)))  # type: ignore[return-value]


def _tessellate(part: PartGeometry) -> tuple[dict[str, Any], dict[str, Any]]:
    """One part's tessellation and the view entry template for it."""
    if part.shape is not None:
        from ocp_tessellate.convert import tessellate_group, to_ocpgroup

        group, instances = to_ocpgroup(flat(part.shape), names=[part.name])
        meshed, shapes, _ = tessellate_group(group, instances)
        (leaf,) = _leaves(shapes)
        return meshed[0], leaf
    scene = trimesh.Scene()
    scene.add_geometry(part.mesh, node_name=part.name, geom_name=part.name)
    data = cadview.mesh_view_parts(scene)
    return data[0][0], data[1][0]


def _leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    if "parts" in node:
        return [leaf for child in node["parts"] for leaf in _leaves(child)]
    return [node]


def write_view(built: BuiltDesign, target: Path) -> None:
    from ocp_tessellate.utils import numpy_to_buffer_json

    meshes: list[dict[str, Any]] = []
    refs: dict[int, tuple[int, dict[str, Any]]] = {}
    root = built.spec.name.replace("/", "_")

    def part_entry(part: PartGeometry, path: str, name: str, transform: fr.Frame) -> dict[str, Any]:
        if id(part) not in refs:
            mesh, template = _tessellate(part)
            meshes.append(mesh)
            refs[id(part)] = (len(meshes) - 1, template)
        ref, template = refs[id(part)]
        return {**template, "id": path, "name": name, "shape": {"ref": ref}, "loc": _loc(transform)}

    def group(design: BuiltDesign, path: str, name: str, transform: fr.Frame) -> dict[str, Any]:
        parts = []
        for address, part_or_component, local in design.instances():
            placed = transform @ local
            if part_or_component in design.components:
                parts.append(group(design.components[part_or_component], f"{path}/{address}", address, placed))
            else:
                parts.append(part_entry(design.parts[part_or_component], f"{path}/{address}", address, placed))
        return {"version": 3, "parts": parts, "loc": IDENTITY_LOC, "name": name, "id": path, "normal_len": 0}

    tree = group(built, f"/{root}", root, fr.IDENTITY)
    lo, hi = built.mesh.bounds if len(built.mesh.faces) else (np.zeros(3), np.zeros(3))
    tree["bb"] = {f"{axis}{end}": float(v[i]) for end, v in (("min", lo), ("max", hi)) for i, axis in enumerate("xyz")}
    data = numpy_to_buffer_json({"instances": meshes, "shapes": tree})
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(target)


def instance_of(view_id: str) -> str:
    """The instance address of a viewer id: '/gearbox/tilt/planet[2]' -> 'tilt/planet[2]'."""
    return view_id.strip("/").split("/", 1)[1]
