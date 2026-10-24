"""3MF for printing: each made part in its own frame (the way it is made), and the placed assembly."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import trimesh

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign

MEDIA = "model/3mf"


class ThreeMfExporter:
    name = "3mf"
    summary = "parts/<part>.3mf for each made part as it is made, and model.3mf of the assembly"

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        out = []
        for name, part in built.parts.items():
            if part.spec.catalog is None:
                rel = f"parts/{name}.3mf"
                part.mesh.export(out_dir / rel)
                out.append((rel, MEDIA))
        scene = trimesh.Scene()
        for leaf in built.leaves():
            scene.add_geometry(leaf.part.mesh, node_name=leaf.path, geom_name=leaf.part.name, transform=leaf.transform)
        scene.export(out_dir / "model.3mf")
        return [*out, ("model.3mf", MEDIA)]
