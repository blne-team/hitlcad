"""The slicers a printer is sliced in: adapters to slicer programs, Bambu Studio first."""

from __future__ import annotations

from hitlcad.builtin.printing.bambu import BambuStudio
from hitlcad.core.project import Slicer
from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_slicers() -> list[Slicer]:
    return [BambuStudio()]
