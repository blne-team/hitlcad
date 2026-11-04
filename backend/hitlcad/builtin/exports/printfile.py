"""The file to print from: every printed part, as many times as the design places it, lying as it prints, with
its settings and filament as its own, written by the project's printer's slicer (`Slicer.write`) in its own terms,
with the page that says how to open it there."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from hitlcad.build.assemble import copies, print_layout
from hitlcad.core.project import ToPrint
from hitlcad.exporters import Unavailable
from hitlcad.kernel.plugins import registry

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign
    from hitlcad.core.report import PlannedPart


class PrintExporter:
    name = "print"
    title = "For the slicer"
    summary = ("print.3mf: every printed part, as many as the design places, lying as it prints with its settings and "
               "filament, for the project's printer's slicer; print-plan.md: how to open it there, and each part's "
               "settings and why")  # fmt: skip

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        printer = built.printer
        if printer is None:
            raise Unavailable("the project has no printer yet: the human enters theirs in the panel's Print tab", None)
        slicer = registry().slicers.get(printer.slicer)
        if slicer is None:
            raise Unavailable(f"the printer is sliced in {printer.slicer!r}, which no installed plugin adapts", None)
        plan: dict[str, PlannedPart] = {p.path: p for p in report["print_plan"]}
        slots, _ = print_layout(built)
        parts = [ToPrint(plan[p.path], p.part.features.pieces, copies(p, middle, plan[p.path].count))
                 for p, middle in slots if p.path in plan]  # fmt: skip
        if not parts:
            raise Unavailable("nothing in the design is printed: no part's process is a printing one")
        return slicer.write(built.spec.name, parts, printer, out_dir)
