from __future__ import annotations

import re
import shutil
import subprocess
from functools import cached_property
from importlib.resources import files
from pathlib import Path
from typing import Any

from hitlcad.backends.base import CADBackend
from hitlcad.backends.workers import Ticket
from hitlcad.config import Settings
from hitlcad.execution.executor import Executor
from hitlcad.geometry import cadview, mesh
from hitlcad.kernel.errors import HitlcadError

ASSEMBLY_FILE = "model.blend"
#: Seconds `blender --version` may take: it starts no UI, and returns in a few seconds even on a cold disk.
VERSION_TIMEOUT_S = 30


class BlenderBackend(CADBackend):
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

    def __init__(self, settings: Settings, executor: Executor | None = None) -> None:
        super().__init__(settings, executor)
        self.binary = settings.blender_binary

    @cached_property
    def version(self) -> str | None:
        exe = shutil.which(self.binary)
        if not exe:
            return None
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, encoding="utf-8",
                                 timeout=VERSION_TIMEOUT_S).stdout  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"Blender (\d+\.\d+(?:\.\d+)?)", out)
        return m.group(1) if m else None

    def code(self) -> list[Path]:
        """Besides the runtime's, the view its parts are tessellated for here (`complete_parts`)."""
        return [*super().code(), Path(cadview.__file__), Path(mesh.__file__)]

    def libraries(self) -> dict[str, str | None]:
        return {"blender": self.version}

    def complete_parts(self, parts: list[dict[str, Any]], work: Path) -> None:
        """Each part's view, from its objects, in hitlcad's Python: Blender's has no trimesh to find a mesh's
        creases with."""
        for part in parts:
            part["view"] = f"parts/{part['name']}{cadview.VIEW_SUFFIX}"
            objects = cadview.object_meshes(cadview.load_mesh(work / part["objects"]))
            cadview.mesh_view(cadview.pieces(part["name"], objects)).write(work / part["view"])

    def availability(self) -> tuple[bool, str]:
        if not shutil.which(self.binary):
            return False, f"Blender executable {self.binary!r} not found on PATH"
        return (True, f"Blender {self.version}") if self.version else (False, "could not determine Blender version")

    def authoring_guide(self) -> str:
        guide = files("hitlcad.builtin.blender").joinpath("guide.md").read_text(encoding="utf-8")
        return guide.replace("{blender_version}", self.version or "unknown")

    async def write_assembly(
        self, ticket: Ticket, design: Path, tree: dict[str, Any], out_dir: Path, work: Path
    ) -> tuple[str, str]:
        """`model.blend`: the assembly as one Blender scene, from the parts' files, in one of the design's workers."""
        target = out_dir.resolve() / ASSEMBLY_FILE
        result, _ = await self.job(ticket, design, work, {"mode": "assembly", "tree": tree, "target": str(target)})
        if not result["ok"]:
            raise HitlcadError(result["error"])
        return ASSEMBLY_FILE, "application/x-blender"

    def command(self) -> list[str]:
        return [self.binary, "--background", "--factory-startup", "--offline-mode", "-noaudio", "--python-exit-code",
                "1", "--python", str(self.entry)]  # fmt: skip
