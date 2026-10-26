"""The build123d backend: exact B-rep solid modelling on OpenCASCADE, in hitlcad's own Python."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hitlcad.kernel.plugins import BackendFactory, hookimpl

if TYPE_CHECKING:
    from hitlcad.backends.base import CADBackend
    from hitlcad.config import Settings


def _build123d(settings: Settings) -> CADBackend:
    from hitlcad.builtin.build123d.backend import Build123dBackend

    return Build123dBackend(timeout_s=settings.build_timeout_s, memory_mb=settings.build_memory_mb,
                            mesh_tolerance_mm=settings.mesh_tolerance_mm,
                            mesh_angular_tolerance=settings.mesh_angular_tolerance, warm=settings.warm_builds)  # fmt: skip


@hookimpl
def hitlcad_backends() -> dict[str, BackendFactory]:
    return {"build123d": _build123d}
