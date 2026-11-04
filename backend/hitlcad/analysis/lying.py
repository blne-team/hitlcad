"""Printed parts lying as they print: every printed part of a build, its components' included, and where each
lies on a bed of its own, side by side, as the print view draws them and the file to print from places them."""

from __future__ import annotations

import itertools
from typing import NamedTuple

import numpy as np

from hitlcad.analysis.model import BuiltDesign, PartGeometry
from hitlcad.core import frames as fr
from hitlcad.core.checks import PrintedProcess
from hitlcad.geometry import cadview

#: The gap the print view leaves between two parts on the bed, mm.
PRINT_GAP_MM = 15.0


class Printed(NamedTuple):
    """A printed part of a build, its components' included: by its path ("fan/blade"), in the built design that
    has it, under its name there."""

    path: str
    built: BuiltDesign
    name: str
    process: PrintedProcess

    @property
    def part(self) -> PartGeometry:
        return self.built.parts[self.name]


def printed(built: BuiltDesign, prefix: str = "") -> list[Printed]:
    """Every printed part of a build, its components' included, each once."""
    out = []
    for name in built.parts:
        if isinstance(process := built.spec.process(name), PrintedProcess):
            out.append(Printed(prefix + name, built, name, process))
    for name, component in built.components.items():
        out += printed(component, f"{prefix}{name}/")
    return out


def print_layout(built: BuiltDesign) -> tuple[list[tuple[Printed, float]], list[list[float]]]:
    """Where the print view lays each printed part, lying as it prints, side by side along X: the middle of its slot
    (`lying`); with the project's printer, each slot is a bed of its own, the beds (x0, y0, x1, y1) beside."""
    printer = built.printer
    slots, beds, x = [], [], 0.0
    for p in printed(built):
        lo, hi = _box(p, p.process)
        width = hi[0] - lo[0]
        if printer:  # its bed, and the part in its middle
            bed_x, bed_y = printer.build_volume_mm[:2]
            beds.append([x, -bed_y / 2, x + bed_x, bed_y / 2])
            middle, width = x + bed_x / 2, max(width, bed_x)
        else:
            middle = x + width / 2
        slots.append((p, middle))
        x += width + PRINT_GAP_MM
    return slots, beds


def _box(p: Printed, process: PrintedProcess) -> tuple[np.ndarray, np.ndarray]:
    """The box round a part lying as `process` makes it, from its view's bounds."""
    bounds = np.array(cadview.PartView.header(p.part.view_file.read_bytes())["bounds"])
    corners = fr.transform_points(np.array(list(itertools.product(*bounds.T))), process.made())
    return corners.min(axis=0), corners.max(axis=0)


def lying(p: Printed, process: PrintedProcess, middle: float) -> fr.Frame:
    """A printed part lying as `process` makes it (any way up), on Z = 0 in the middle of its slot."""
    lo, hi = _box(p, process)
    return fr.translation((middle - (lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])) @ process.made()


def copies(p: Printed, middle: float, count: int) -> list[fr.Frame]:
    """`count` copies of a printed part lying as it prints in the middle of its slot (`lying`), side by side along Y."""
    lo, hi = _box(p, p.process)
    step = hi[1] - lo[1] + PRINT_GAP_MM
    first = lying(p, p.process, middle)
    return [fr.translation((0.0, (k - (count - 1) / 2) * step, 0.0)) @ first for k in range(count)]
