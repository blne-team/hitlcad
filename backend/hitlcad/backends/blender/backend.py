from __future__ import annotations

import re
import shutil
import subprocess
from functools import cached_property
from importlib.resources import files
from pathlib import Path
from typing import Any

from hitlcad.backends.subprocess_backend import SubprocessBackend

ASSEMBLY_FILE = "model.blend"


class BlenderBackend(SubprocessBackend):
    name = "blender"
    title = "Blender (mesh)"
    summary = (
        "Polygon-mesh modelling with Blender's bpy/bmesh, modifiers and geometry nodes. Best for "
        "organic, sculptural, decorative or freeform shapes: figurines, vases, ornaments, "
        "stylised products, terrain, twisted or smoothed forms. Output is a mesh (no STEP): its "
        "connectors' holes, shafts and faces, and so fits, are measured on the mesh to within its facets; "
        "inside corner radii (CNC milling, sheet metal) cannot be."
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

    async def write_assembly(self, tree: dict[str, Any], out_dir: Path, work: Path) -> tuple[str, str]:
        """`model.blend`: the assembly as one Blender scene, from the parts' files, in one Blender process."""
        target = out_dir.resolve() / ASSEMBLY_FILE
        result, _ = await self._run(work, {"mode": "assembly", "tree": tree, "target": str(target)})
        if not result["ok"]:
            raise RuntimeError(f"{ASSEMBLY_FILE}: {result['error']}")
        return ASSEMBLY_FILE, "application/x-blender"

    def command(self, job_file: Path) -> list[str]:
        return [
            self.binary, "--background", "--factory-startup", "--offline-mode", "-noaudio",
            "--python-exit-code", "1", "--python", str(self.entry), "--", str(job_file),
        ]  # fmt: skip
