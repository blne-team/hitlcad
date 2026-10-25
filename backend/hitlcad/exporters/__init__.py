"""Exporters: files made from a build, chosen per design by design.json `outputs`.

An exporter implements `Exporter` and registers under the ``hitlcad.exporters`` entry-point group (a
factory returning the exporter). hitlcad ships `bom`, `3mf`, `urdf` and `sdf`. The assembly STEP, the
parts' own STEP and STL, the mesh and the 3D view are written on every build and need no exporter.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign

log = logging.getLogger(__name__)


class Exporter(Protocol):
    name: str
    #: What its files are, for the panel's file list: "Bill of materials".
    title: str
    #: One line for the agent: what the files are for.
    summary: str

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        """Write files under `out_dir`; return (relative path, media type) of each."""
        ...


def builtin() -> dict[str, Exporter]:
    from hitlcad.exporters.bom import BomExporter
    from hitlcad.exporters.robot import SdfExporter, UrdfExporter
    from hitlcad.exporters.threemf import ThreeMfExporter

    return {e.name: e for e in (BomExporter(), ThreeMfExporter(), UrdfExporter(), SdfExporter())}


def discover() -> dict[str, Exporter]:
    found = builtin()
    for ep in entry_points(group="hitlcad.exporters"):
        try:
            exporter = ep.load()()
            found[exporter.name] = exporter
        except Exception:  # a broken plugin must not take the service down
            log.exception("failed to load exporter %s", ep.name)
    return found
