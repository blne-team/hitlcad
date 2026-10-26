"""The laser cutting process: a part is a flat plate of the sheet, cut through along Z of its own frame."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from hitlcad.core.checks import Process, ProcessFinding
from hitlcad.kernel.errors import Unmeasurable

if TYPE_CHECKING:
    from hitlcad.analysis.model import Body


class LaserCutProcess(Process):
    """Laser cutting from a sheet `thickness_mm` thick, lying flat in the part's own frame (Z through the sheet):
    the part is a plate of that thickness, and no hole is narrower than the sheet is thick (a narrower one burns
    out or closes up). Holes come out `kerf_mm` larger than drawn and outlines `kerf_mm` smaller, for fit
    checks."""

    process: Literal["laser_cut"]
    thickness_mm: float = Field(gt=0)
    kerf_mm: float = Field(default=0.15, ge=0, description="Width of material the beam takes")
    example = {"process": "laser_cut", "thickness_mm": 3}
    findings = {
        "laser_flat": ("A laser-cut part that is not a plate of the sheet's thickness",
                       "Model the part as a plate of the sheet's thickness lying flat in its own frame: a profile "
                       "extruded along Z (hitlcad_lasercut.blocks.plate)."),
        "laser_holes": ("A hole narrower than the sheet is thick",
                        "Make every hole at least as wide as the sheet is thick, or cut it another way."),
    }  # fmt: skip

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        return -self.kerf_mm  # a hole comes out larger, an outline smaller

    def checks(self, body: Body) -> list[ProcessFinding]:
        return [self._flat(body), self._holes(body)]

    def _flat(self, body: Body) -> ProcessFinding:
        z = body.metrics.size[2]
        ok = abs(z - self.thickness_mm) <= body.settings.mesh_tolerance_mm + 1e-6
        return ProcessFinding(name="laser_flat", passed=ok, detail=f"{z:.2f} mm along Z, the sheet is "
                              f"{self.thickness_mm:g} mm")  # fmt: skip

    def _holes(self, body: Body) -> ProcessFinding:
        try:
            holes = body.holes()
        except Unmeasurable as e:
            return ProcessFinding(name="laser_holes", passed=None, detail=str(e))
        narrow = sorted(h.diameter for h in holes if h.diameter < self.thickness_mm)
        if narrow:
            return ProcessFinding(name="laser_holes", passed=False, detail=f"{len(narrow)} holes narrower than the "
                                  f"{self.thickness_mm:g} mm sheet: " + ", ".join(f"Ø{d:.2f}" for d in narrow))  # fmt: skip
        return ProcessFinding(
            name="laser_holes", passed=True, detail=f"{len(holes)} holes, none narrower than the sheet"
        )
