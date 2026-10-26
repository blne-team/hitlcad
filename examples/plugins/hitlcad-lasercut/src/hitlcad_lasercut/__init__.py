"""Laser cutting for hitlcad: the `laser_cut` process, the `plate` building block and a skill reference.

This module stays light: hitlcad imports it while it builds design.json's unions, so what a hook returns is
imported inside the hook.
"""

from __future__ import annotations

from pathlib import Path

from hitlcad.kernel.plugins import ModelPackage, hookimpl


@hookimpl
def hitlcad_processes() -> list[type]:
    from hitlcad_lasercut.process import LaserCutProcess

    return [LaserCutProcess]


@hookimpl
def hitlcad_model_packages() -> list[ModelPackage]:
    return [ModelPackage(root=Path(__file__).parents[1], package="hitlcad_lasercut.blocks", backends=("build123d",))]


@hookimpl
def hitlcad_skill_references() -> dict[str, str]:
    return {"lasercut.md": (Path(__file__).with_name("lasercut.md")).read_text(encoding="utf-8")}
