"""What a built design becomes on disk: the assembly as a STEP product structure, as one mesh, as the 3D
view (three-cad-viewer's instanced format), and as the tree a backend writes its own assembly file from.

The view draws each part per instance at the instance's place, nested by component, the way
`analysis.cadview.View` draws any part. Viewer ids are `/<design>/<instance path>`, and "face i" of an
instance is `faces()[i]` of its part's STEP (`parts/<part>.step`), the way picks and the geometry tools
number them. A part in several colours is a group of one shape per colour at its instance's id
(`/<design>/<instance path>/<material>`); mesh picks name a point, which the build resolves to its
instance, so the extra level changes no pick.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hitlcad.analysis import cadview
from hitlcad.analysis.features import Unmeasurable, flat
from hitlcad.analysis.geometry import location
from hitlcad.analysis.model import BuiltDesign, PartGeometry
from hitlcad.core import frames as fr


def step_assembly(built: BuiltDesign, target: Path) -> bool:
    """The assembly as STEP: nested compounds named by instance, each part shared by its instances. False
    (and nothing written) when a part has no exact shape."""
    from build123d import Compound, export_step

    try:
        shapes = {id(part): flat(part.features.exact()) for part in all_parts(built)}
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


def write_view(built: BuiltDesign, target: Path) -> None:
    view = cadview.View()
    root = built.spec.name.replace("/", "_")

    def group(design: BuiltDesign, path: str, name: str, transform: fr.Frame) -> dict[str, Any]:
        parts = []
        for address, part_or_component, local in design.instances():
            placed = transform @ local
            if part_or_component in design.components:
                parts.append(group(design.components[part_or_component], f"{path}/{address}", address, placed))
            else:
                parts.append(view.part(design.parts[part_or_component].features, f"{path}/{address}", address, placed))
        return cadview.group(path, name, parts)

    view.write(group(built, f"/{root}", root, fr.IDENTITY), built.mesh.bounds, target)
