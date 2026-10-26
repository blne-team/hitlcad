"""The manufacturing processes: FDM and resin printing, 3-axis CNC milling, sheet metal and injection molding."""

from __future__ import annotations

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_processes() -> list[type]:
    from hitlcad.builtin.processes import processes as p

    return [p.FdmProcess, p.ResinProcess, p.CncMillingProcess, p.SheetMetalProcess, p.InjectionMoldingProcess]
