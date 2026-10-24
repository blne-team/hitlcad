from __future__ import annotations

import re
import shutil
import subprocess
from functools import cached_property
from importlib.resources import files
from pathlib import Path

from hitlcad.backends.subprocess_backend import SubprocessBackend


class BlenderBackend(SubprocessBackend):
    name = "blender"
    title = "Blender (mesh)"
    summary = (
        "Polygon-mesh modelling with Blender's bpy/bmesh, modifiers and geometry nodes. Best for "
        "organic, sculptural, decorative or freeform shapes: figurines, vases, ornaments, "
        "stylised products, terrain, twisted or smoothed forms. Output is a mesh (no STEP); "
        "dimensional precision is lower than B-rep. Connector frames are checked; features measured on "
        "B-rep (hole and shaft sizes, fits) are reported unverified."
    )
    brep = False
    entry = Path(__file__).with_name("entry.py")

    def __init__(self, *, binary: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.binary = binary

    @cached_property
    def version(self) -> str | None:
        exe = shutil.which(self.binary)
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"Blender (\d+\.\d+(?:\.\d+)?)", out)
        return m.group(1) if m else None

    def availability(self) -> tuple[bool, str]:
        if not shutil.which(self.binary):
            return False, f"Blender executable {self.binary!r} not found on PATH"
        return (True, f"Blender {self.version}") if self.version else (False, "could not determine Blender version")

    def authoring_guide(self) -> str:
        guide = files("hitlcad.backends.blender").joinpath("guide.md").read_text(encoding="utf-8")
        return guide.replace("{blender_version}", self.version or "unknown")

    def command(self, job_file: Path) -> list[str]:
        return [
            self.binary, "--background", "--factory-startup", "--offline-mode", "-noaudio",
            "--python-exit-code", "1", "--python", str(self.entry), "--", str(job_file),
        ]  # fmt: skip
