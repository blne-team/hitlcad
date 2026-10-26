"""The standard parts catalog (bd_warehouse: ISO and ASME fasteners, inserts, bearings) and vendor STEP files:
their providers, which `search_catalog` searches, and the package part files import them from."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hitlcad.kernel.plugins import ModelPackage, hookimpl


@hookimpl
def hitlcad_catalogs() -> list[Any]:
    from hitlcad.builtin.catalogs import bd_warehouse, step

    return [bd_warehouse.PROVIDER, step.PROVIDER]


@hookimpl
def hitlcad_model_packages() -> list[ModelPackage]:
    return [ModelPackage(root=Path(__file__).parents[3], package="hitlcad.builtin.catalogs", backends=("build123d",))]
