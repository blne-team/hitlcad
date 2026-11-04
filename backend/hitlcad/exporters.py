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


class Unavailable(Exception):
    """What an exporter raises when this build cannot have its files: why, and the verdict on the design's asking for
    them (False: the design cannot have them; None: they wait on what only the human can give, such as their
    printer)."""

    def __init__(self, why: str, passed: bool | None = False) -> None:
        super().__init__(why)
        self.passed = passed


class Exporter(Protocol):
    name: str
    #: What its files are, for the panel's file list: "Bill of materials".
    title: str
    #: One line for the agent: what the files are for.
    summary: str

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        """Write files under `out_dir`; return (relative path, media type) of each, or raise `Unavailable`."""
        ...


def discover() -> dict[str, Exporter]:
    return registry().exporters
