"""The Blender backend: polygon-mesh modelling with bpy, in Blender's own Python."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hitlcad.kernel.plugins import BackendFactory, hookimpl

if TYPE_CHECKING:
    from hitlcad.backends.base import CADBackend
    from hitlcad.config import Settings


def _blender(settings: Settings) -> CADBackend:
    from hitlcad.builtin.blender.backend import BlenderBackend

    return BlenderBackend(binary=settings.blender_binary, timeout_s=settings.build_timeout_s,
                          memory_mb=settings.build_memory_mb, mesh_tolerance_mm=settings.mesh_tolerance_mm,
                          mesh_angular_tolerance=settings.mesh_angular_tolerance, warm=settings.warm_builds)  # fmt: skip


@hookimpl
def hitlcad_backends() -> dict[str, BackendFactory]:
    return {"blender": _blender}
