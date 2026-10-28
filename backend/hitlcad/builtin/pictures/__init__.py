"""Designing from a picture: the silhouette check, and how an agent models against a reference."""

from __future__ import annotations

from pathlib import Path

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_checks() -> list[type]:
    from hitlcad.builtin.pictures.check import SilhouetteCheck

    return [SilhouetteCheck]


@hookimpl
def hitlcad_skill_references() -> dict[str, str]:
    return {"pictures.md": Path(__file__).with_name("pictures.md").read_text(encoding="utf-8")}
