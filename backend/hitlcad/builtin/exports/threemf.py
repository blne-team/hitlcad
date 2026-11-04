"""3MF of the placed assembly: every instance an object named by its path, sharing its part's meshes, in their
colours (`geometry.threemf`). A file to print from is the `print` output's, in the slicer's own terms."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from hitlcad.geometry.threemf import MEDIA, ThreeMf

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign


class ThreeMfExporter:
    name = "3mf"
    title = "3MF of the assembly"
    summary = "model.3mf: the placed assembly, each instance an object, in its colours"

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        model = ThreeMf()
        for leaf in built.leaves():
            model.place(leaf.part.features.pieces, leaf.path, leaf.transform)
        model.write(out_dir / "model.3mf")
        return [("model.3mf", MEDIA)]
