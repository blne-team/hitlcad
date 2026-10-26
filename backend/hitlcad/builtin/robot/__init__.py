"""A design as a robot description, for simulators: URDF and SDF."""

from __future__ import annotations

from typing import Any

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_exporters() -> list[Any]:
    from hitlcad.builtin.robot.robot import SdfExporter, UrdfExporter

    return [UrdfExporter(), SdfExporter()]
