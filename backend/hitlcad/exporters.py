"""Exporters: files made from a build, chosen per design by design.json `outputs`.

An exporter implements `Exporter`, and a plugin gives it from its `hitlcad_exporters` hook. hitlcad's own are
`bom` and `3mf` (`hitlcad.builtin.exports`), `urdf` and `sdf` (`hitlcad.builtin.robot`). The assembly STEP, the
parts' own STEP and STL, the mesh and the 3D view are written on every build and need no exporter.
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
