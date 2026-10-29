"""Exporters: files made from a build, chosen per design by design.json `outputs`.

An exporter implements `Exporter`, and a plugin gives it from its `hitlcad_exporters` hook. hitlcad's own are
`bom` and `3mf` (`hitlcad.builtin.exports`), `urdf` and `sdf` (`hitlcad.builtin.robot`). The build writes the
assembly STEP, its mesh, each backend's own assembly file and the rendered views itself, when `outputs` names them
(`build.pipeline.OUTPUTS`, whose names an exporter cannot take); the parts' own files and the 3D view it writes always.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from hitlcad.kernel.plugins import registry

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign


class Exporter(Protocol):
    name: str
    #: What its files are, for the panel's file list: "Bill of materials".
    title: str
    #: One line for the agent: what the files are for.
    summary: str

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        """Write files under `out_dir`; return (relative path, media type) of each."""
        ...


def discover() -> dict[str, Exporter]:
    return registry().exporters
