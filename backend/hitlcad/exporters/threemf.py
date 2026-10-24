"""3MF for printing: each made part in its own frame (the way it is made), and the placed assembly.

A part is an object made of one mesh per colour (`build.assemble.pieces`), each mesh coloured by a base
material and named by its materials, so a multi-colour slicer gets a part per colour to assign a filament
to. In `model.3mf` every instance is an object named by its path, sharing its part's meshes.
"""

from __future__ import annotations

import locale
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from lib3mf import Lib3MF

from hitlcad.build.assemble import pieces
from hitlcad.core import frames as fr

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign, PartGeometry

MEDIA = "model/3mf"


class ThreeMfExporter:
    name = "3mf"
    summary = "parts/<part>.3mf for each made part as it is made, and model.3mf of the assembly, in their colours"

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        out = []
        for name, part in built.parts.items():
            if part.spec.catalog is None:
                rel = f"parts/{name}.3mf"
                model = _Model()
                model.place(part, name, fr.IDENTITY)
                model.write(out_dir / rel)
                out.append((rel, MEDIA))
        model = _Model()
        for leaf in built.leaves():
            model.place(leaf.part, leaf.path, leaf.transform)
        model.write(out_dir / "model.3mf")
        return [*out, ("model.3mf", MEDIA)]


class _Model:
    """A 3MF model (millimetres) being written: parts' meshes once each, colours once each."""

    def __init__(self) -> None:
        self.wrapper = Lib3MF.Wrapper(str(Path(Lib3MF.__file__).with_name("lib3mf")))
        self.model = self.wrapper.CreateModel()
        self.materials: Any = None
        self.colors: dict[str, int] = {}
        self.meshes: dict[int, list[Any]] = {}

    def place(self, part: PartGeometry, name: str, transform: fr.Frame) -> None:
        """An object `name` made of the part's meshes, placed by `transform`."""
        if id(part) not in self.meshes:
            self.meshes[id(part)] = [self._mesh(piece.name, piece.mesh, piece.color) for piece in pieces(part)]
        placed = self.model.AddComponentsObject()
        placed.SetName(name)
        for mesh in self.meshes[id(part)]:
            placed.AddComponent(mesh, self.wrapper.GetIdentityTransform())
        self.model.AddBuildItem(placed, _transform(transform))

    def write(self, target: Path) -> None:
        # lib3mf's writer switches the process to the C locale and leaves it there, which would make ASCII the
        # default encoding of every text file this process writes after it
        saved = locale.setlocale(locale.LC_ALL)
        try:
            self.model.QueryWriter("3mf").WriteToFile(str(target))
        finally:
            locale.setlocale(locale.LC_ALL, saved)

    def _mesh(self, name: str, mesh: Any, color: str | None) -> Any:
        faces = np.asarray(mesh.faces, dtype=np.uint32)
        # 3MF has no triangle that repeats a vertex (lib3mf refuses one); merging a mesh's vertices can make some
        faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 2] != faces[:, 0])]
        obj = self.model.AddMeshObject()
        obj.SetName(name)
        obj.SetGeometry(
            (Lib3MF.Position * len(mesh.vertices)).from_buffer_copy(np.ascontiguousarray(mesh.vertices, np.float32)),
            (Lib3MF.Triangle * len(faces)).from_buffer_copy(faces),
        )
        if color is not None:
            obj.SetObjectLevelProperty(*self._color(name, color))
        return obj

    def _color(self, name: str, color: str) -> tuple[int, int]:
        """(base-material group, index) of a colour, added under the name of the first mesh in it."""
        if self.materials is None:  # 3MF has no empty base-material group: made with the first colour
            self.materials = self.model.AddBaseMaterialGroup()
        if color not in self.colors:
            rgb = (int(color[i : i + 2], 16) for i in (1, 3, 5))
            self.colors[color] = self.materials.AddMaterial(name, self.wrapper.RGBAToColor(*rgb, 255))
        return self.materials.GetResourceID(), self.colors[color]


def _transform(m: fr.Frame) -> Any:
    """A 3MF transform: the 4 × 3 matrix applied to row vectors, so the frame's top three rows transposed."""
    return Lib3MF.Transform.from_buffer_copy(np.ascontiguousarray(m[:3, :].T, np.float32))
