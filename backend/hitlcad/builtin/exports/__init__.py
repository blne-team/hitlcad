"""Files made from a build: the bill of materials, the assembly as 3MF, and the file to print from."""

from __future__ import annotations

from typing import Any

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_exporters() -> list[Any]:
    from hitlcad.builtin.exports.bom import BomExporter
    from hitlcad.builtin.exports.printfile import PrintExporter
    from hitlcad.builtin.exports.threemf import ThreeMfExporter

    return [BomExporter(), ThreeMfExporter(), PrintExporter()]
