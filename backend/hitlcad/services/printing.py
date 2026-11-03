"""Preparing a design for printing: the project's printer, which the human enters in the panel (no agent turn)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from hitlcad.core.checks import PrintedProcess
from hitlcad.core.project import Printer
from hitlcad.kernel.errors import REQUEST_INVALID
from hitlcad.kernel.model import Model
from hitlcad.kernel.plugins import registry
from hitlcad.services.builds import BuildService
from hitlcad.services.designs import DesignService, readable
from hitlcad.workspace import projectfile


class SlicerInfo(Model):
    name: str
    title: str


class PrinterSetup(Model):
    """What the panel needs to show or ask for a design's printer."""

    file: str | None = Field(description="The project's hitlcad.toml; none when the design is in no project")
    printer: Printer | None
    slicers: list[SlicerInfo] = Field(description="The slicers the installed plugins adapt")
    materials: list[str] = Field(description="The materials of the design's printed parts: a filament for each")


class PrintingService:
    def __init__(self, designs: DesignService, builds: BuildService) -> None:
        self.designs = designs
        self.builds = builds

    def setup(self, design_id: str) -> PrinterSetup:
        folder = self.designs.folder(design_id).path
        file = projectfile.find(folder)
        design = readable(self.designs.files(design_id))
        printed = (
            [s.material for n, s in design.parts.items() if isinstance(design.process(n), PrintedProcess)]
            if design
            else []
        )
        return PrinterSetup(file=str(file) if file else None, printer=projectfile.printer(folder),
                            slicers=[SlicerInfo(name=s.name, title=s.title) for s in registry().slicers.values()],
                            materials=sorted({m for m in printed if m}))  # fmt: skip

    def set_printer(self, design_id: str, printer: Printer) -> PrinterSetup:
        """Keep the human's printer in the design's project, and build every open design of that project for it."""
        folder = self.designs.folder(design_id).path
        file = projectfile.find(folder)
        if file is None:
            raise REQUEST_INVALID(f"{folder} is in no project (no {projectfile.PROJECT_FILE} in it or its parents): "
                                  "`hitlcad init` or New Design Project makes one")  # fmt: skip
        if printer.slicer not in registry().slicers:
            raise REQUEST_INVALID(f"no slicer {printer.slicer!r}; slicers: {', '.join(registry().slicers)}")
        projectfile.set_printer(file, printer)
        for path, other in self.designs.folders().items():
            if projectfile.find(Path(path)) == file:
                self.builds.schedule_build(other)
        return self.setup(design_id)
