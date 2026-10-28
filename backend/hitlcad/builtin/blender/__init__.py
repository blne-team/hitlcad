"""The Blender backend: polygon-mesh modelling with bpy, in Blender's own Python."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from hitlcad.kernel.plugins import BackendFactory, ModelPackage, hookimpl

if TYPE_CHECKING:
    from hitlcad.backends.base import CADBackend
    from hitlcad.config import Settings


def _blender(settings: Settings) -> CADBackend:
    from hitlcad.builtin.blender.backend import BlenderBackend

    return BlenderBackend(settings)


@hookimpl
def hitlcad_backends() -> dict[str, BackendFactory]:
    return {"blender": _blender}


@hookimpl
def hitlcad_model_packages() -> list[ModelPackage]:
    return [ModelPackage(root=Path(__file__).with_name("models"), package="hitlcad_blender", backends=("blender",),
                         summary="section_form: an open hollow form from height/radius sections, with a closed material boundary")]  # fmt: skip


@hookimpl
def hitlcad_skill_references() -> dict[str, str]:
    return {"builders-blender.md": Path(__file__).with_name("builders.md").read_text(encoding="utf-8")}
