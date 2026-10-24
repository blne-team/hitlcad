"""Preview renderer interface and the default Blender Workbench implementation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from hitlcad.backends.executor import Executor, LocalExecutor
from hitlcad.core.types import MeshMetrics

ENTRY = Path(__file__).with_name("blender_entry.py")
PREVIEW_FILE = "preview.png"


class Renderer(Protocol):
    async def render(self, mesh_stl: Path, out_dir: Path, metrics: MeshMetrics | None = None) -> Path | None:
        """Write a preview image for ``mesh_stl`` into ``out_dir``; return its path or None."""
        ...


class NullRenderer:
    async def render(self, mesh_stl: Path, out_dir: Path, metrics: MeshMetrics | None = None) -> Path | None:
        return None


class BlenderWorkbenchRenderer:
    """Four orthographic views (iso, front, right, top) composed into one labelled sheet."""

    def __init__(
        self, *, binary: str = "blender", size: int = 512, executor: Executor | None = None, timeout_s: float = 120
    ) -> None:
        self.binary, self.size, self.timeout_s = binary, size, timeout_s
        self.executor = executor or LocalExecutor()

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    async def render(self, mesh_stl: Path, out_dir: Path, metrics: MeshMetrics | None = None) -> Path | None:
        work = out_dir / "_render"
        work.mkdir(parents=True, exist_ok=True)
        argv = [self.binary, "--background", "--factory-startup", "--offline-mode", "-noaudio",
                "--python-exit-code", "1", "--python", str(ENTRY), "--", str(mesh_stl.resolve()), str(self.size)]  # fmt: skip
        result = await self.executor.run(argv, work, timeout_s=self.timeout_s)
        meta_file = work / "views.json"
        if result.returncode != 0 or not meta_file.is_file():
            (out_dir / "render.log").write_text(result.output, encoding="utf-8")
            return None
        views = json.loads(meta_file.read_text())["views"]
        sheet = compose_sheet([(v["name"], work / v["file"]) for v in views], self.size, metrics)
        target = out_dir / PREVIEW_FILE
        sheet.save(target, optimize=True)
        shutil.rmtree(work, ignore_errors=True)
        return target


def compose_sheet(views: list[tuple[str, Path]], size: int, metrics: MeshMetrics | None) -> Image.Image:
    cols = 2
    rows = (len(views) + cols - 1) // cols
    footer = 28
    sheet = Image.new("RGB", (cols * size, rows * size + footer), (246, 246, 243))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(12, size // 28))
    for i, (name, path) in enumerate(views):
        x, y = (i % cols) * size, (i // cols) * size
        with Image.open(path) as im:
            sheet.paste(im.convert("RGB").resize((size, size)), (x, y))
        draw.text((x + 10, y + 8), name.upper(), fill=(40, 40, 40), font=font)
        draw.rectangle([x, y, x + size - 1, y + size - 1], outline=(210, 210, 205))
    if metrics:
        sx, sy, sz = metrics.size
        caption = f"bounding box X {sx:.2f} x Y {sy:.2f} x Z {sz:.2f} mm   ·   bodies {metrics.bodies}   ·   " + (
            "watertight" if metrics.watertight else "NOT watertight"
        )
        draw.text((10, rows * size + 6), caption, fill=(60, 60, 60), font=font)
    return sheet
