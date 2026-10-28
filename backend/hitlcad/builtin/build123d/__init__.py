"""The build123d backend: exact B-rep solid modelling on OpenCASCADE, in hitlcad's own Python."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from hitlcad.kernel.plugins import BackendFactory, ModelPackage, hookimpl

if TYPE_CHECKING:
    from hitlcad.backends.base import CADBackend
    from hitlcad.config import Settings


def _build123d(settings: Settings) -> CADBackend:
    from hitlcad.builtin.build123d.backend import Build123dBackend

    return Build123dBackend(settings)


@hookimpl
def hitlcad_backends() -> dict[str, BackendFactory]:
    return {"build123d": _build123d}


@hookimpl
def hitlcad_model_packages() -> list[ModelPackage]:
    return [ModelPackage(root=Path(__file__).with_name("models"), package="hitlcad_build123d", backends=("build123d",),
                         summary="mounting_plate: a plate with round through holes and optional raised screw bosses")]  # fmt: skip


@hookimpl
def hitlcad_skill_references() -> dict[str, str]:
    return {"builders-build123d.md": Path(__file__).with_name("builders.md").read_text(encoding="utf-8")}
