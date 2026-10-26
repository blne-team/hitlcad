"""hitlcad's materials: density, the processes each suits, and the thread engagement a screw needs in it."""

from __future__ import annotations

from pathlib import Path

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_materials() -> list[Path]:
    return [Path(__file__).with_name("materials.json")]
