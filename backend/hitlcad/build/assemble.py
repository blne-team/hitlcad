"""What a built design becomes on disk: the assembly as a STEP product structure, as one mesh, as the 3D
view (three-cad-viewer's instanced format), and as the tree a backend writes its own assembly file from.

The view draws each part per instance at the instance's place, nested by component, the way
`geometry.cadview.Scene` draws any part. Viewer ids are `/<design>/<instance path>`, and "face i" of an
instance is `faces()[i]` of its part's STEP (`parts/<part>.step`), the way picks and the geometry tools
number them. A part in several colours is a group of one shape per colour at its instance's id
(`/<design>/<instance path>/<material>`); mesh picks name a point, which the build resolves to its
instance, so the extra level changes no pick.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import numpy as np

from hitlcad.analysis.model import BuiltDesign, PartGeometry
from hitlcad.core import frames as fr
from hitlcad.core.checks import PrintedProcess
from hitlcad.geometry import cadview
from hitlcad.kernel.errors import Unmeasurable
from hitlcad.review.measure import location


def step_assembly(built: BuiltDesign, target: Path) -> bool:
    """The assembly as STEP: nested compounds named by instance, each part shared by its instances. False
    (and nothing written) when a part has no exact shape."""
    from build123d import Compound, export_step

    try:
        shapes = {id(part): cadview.flat(part.features.exact()) for part in all_parts(built)}
    except Unmeasurable:
        return False

    def node(design: BuiltDesign, name: str) -> Any:
        children = []
        for address, part_or_component, local in design.instances():
            if part_or_component in design.components:
                child = node(design.components[part_or_component], address)
                child.location = location(local)
            else:
                child = shapes[id(design.parts[part_or_component])].moved(location(local))
                child.label = address
            children.append(child)
        return Compound(children=children, label=name)

    export_step(node(built, built.spec.name), str(target))
    return True


def all_parts(built: BuiltDesign) -> list[PartGeometry]:
    """The parts of a design and of its components."""
    return [*built.parts.values(), *(p for c in built.components.values() for p in all_parts(c))]


def tree(built: BuiltDesign) -> dict[str, Any]:
    """The assembly as a backend's own file takes it (`CADBackend.write_assembly`): each design's parts by their
    named objects in their own frames (`parts/<part>.glb`), its components alike, and its instances with their
    placements."""
    return {
        "name": built.spec.name,
        "parts": {name: str(part.objects_file.resolve()) for name, part in built.parts.items()},
        "components": {name: tree(child) for name, child in built.components.items()},
        "instances": [{"name": address, "of": name, "transform": fr.as_matrix(local)}
                      for address, name, local in built.instances()],
    }  # fmt: skip


def write_scene(built: BuiltDesign, out_dir: Path) -> None:
    """The 3D view of a build in `out_dir`: its parts' views, its components' included, placed per instance."""
    scene = cadview.Scene(out_dir)
    root = built.spec.name.replace("/", "_")

    def group(design: BuiltDesign, path: str, name: str, transform: fr.Frame) -> dict[str, Any]:
        parts = []
        for address, part_or_component, local in design.instances():
            placed = transform @ local
            if part_or_component in design.components:
                parts.append(group(design.components[part_or_component], f"{path}/{address}", address, placed))
            else:
                parts.append(
                    scene.part(design.parts[part_or_component].view_file, f"{path}/{address}", address, placed)
                )
        return cadview.group(path, name, parts)

    scene.write(group(built, f"/{root}", root, fr.IDENTITY))


#: The gap the print view leaves between two parts on the bed, mm.
PRINT_GAP_MM = 15.0


def printed(built: BuiltDesign, prefix: str = "") -> list[tuple[str, PartGeometry, PrintedProcess]]:
    """Every printed part of a build, its components' included, each once: by its path ("fan/blade"), with its
    process."""
    out = []
    for name, part in built.parts.items():
        if isinstance(process := built.spec.process(name), PrintedProcess):
            out.append((prefix + name, part, process))
    for name, component in built.components.items():
        out += printed(component, f"{prefix}{name}/")
    return out


def write_print_scene(built: BuiltDesign, out_dir: Path) -> bool:
    """The print view of a build in `out_dir`: its printed parts, each once, lying as it prints (`Process.made`),
    side by side along X on Z = 0. False, and no file, where nothing is printed."""
    scene, placed, x = cadview.Scene(out_dir), [], 0.0
    for name, part, process in printed(built):
        made = process.made()
        bounds = np.array(cadview.PartView.header(part.view_file.read_bytes())["bounds"])
        corners = fr.transform_points(np.array(list(itertools.product(*bounds.T))), made)
        lo, hi = corners.min(axis=0), corners.max(axis=0)
        at = fr.translation((x - lo[0], -(lo[1] + hi[1]) / 2, -lo[2])) @ made
        placed.append(scene.part(part.view_file, f"/print/{name}", name, at))
        x += hi[0] - lo[0] + PRINT_GAP_MM
    if placed:
        scene.write(cadview.group("/print", "print", placed), cadview.PRINT_SCENE_FILE)
    return bool(placed)
