"""Pictures of what a design should look like: the subject cut out of its background, placed in the design's frame
in millimetres, and compared with the model seen from the same side.

A picture is cut out once per file version: a transparent PNG as it is cut, anything else by separating the subject
from the colour of the picture's border (GrabCut refines a photo; a drawing of outlines is what they enclose).
Outlines are compared on a grid of square cells: how much of their union they share, and how far each strays from
the other, where.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from scipy import ndimage

#: The picture's right and up as (design axis, sign), looking at the design from that side.
VIEWS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "front": ((0, 1), (2, 1)),  # from −Y
    "back": ((0, -1), (2, 1)),  # from +Y
    "right": ((1, 1), (2, 1)),  # from +X
    "left": ((1, -1), (2, 1)),  # from −X
    "top": ((0, 1), (1, 1)),  # from +Z
    "bottom": ((0, -1), (1, 1)),  # from −Z
}
FROM = {"front": "−Y", "back": "+Y", "right": "+X", "left": "−X", "top": "+Z", "bottom": "−Z"}
WORKING_SIZE = 800  # px: the longer side a picture is cut out at
LINE_DRAWING = 0.35  # a subject whose strokes cover less of what they enclose is a drawing of outlines
SPECKLE = 0.05  # pieces smaller than this share of the largest are noise
MAX_CELLS = 1600  # the comparison grid's longer side, at most
FIGURE_SIZE = 900  # px: the figure's longer side
REFERENCE, MODEL, STRAY = (40, 110, 220), (235, 120, 20), (200, 30, 30)


class PictureError(Exception):
    """What is wrong with a picture, said so that the human can fix it."""


def axis(view: str, which: int, sign: int = 1) -> str:
    """'+X': the design axis the picture's right (which=0) or up (1) runs along, signed."""
    a, s = VIEWS[view][which]
    return f"{'+' if s * sign > 0 else '−'}{'XYZ'[a]}"


def plane(points: np.ndarray, view: str) -> np.ndarray:
    """Points of the design's frame as (right, up) in the picture's plane, mm."""
    (h, hs), (v, vs) = VIEWS[view]
    return np.stack([points[:, h] * hs, points[:, v] * vs], axis=1)


# --------------------------------------------------------------------------- cutting out


@dataclass(frozen=True)
class Subject:
    pixels: np.ndarray  # RGB, at most WORKING_SIZE on its longer side, on white where it was transparent
    mask: np.ndarray  # bool: the subject's pixels


def cut_out(path: Path) -> Subject:
    if not path.is_file():
        raise PictureError(f"there is no picture at {path.name}: save it there, or correct `image`")
    stat = path.stat()
    return _cut_out(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=16)
def _cut_out(path: str, _mtime: int, _size: int) -> Subject:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
    im.thumbnail((WORKING_SIZE, WORKING_SIZE))
    rgba = np.asarray(im)
    alpha = rgba[..., 3].astype(np.float32)[..., None] / 255
    pixels = (rgba[..., :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
    cut = rgba[..., 3] < 128
    mask = ~cut if 0.01 < cut.mean() < 0.99 else _separate(rgba[..., :3])
    mask = _clean(mask)
    if not mask.any():
        raise PictureError(f"found no subject in {Path(path).name}: give a picture of it on a plain background, or "
                           "cut out (a PNG with a transparent background)")  # fmt: skip
    return Subject(pixels, mask)


def _separate(rgb: np.ndarray) -> np.ndarray:
    """The subject: what differs from the colour of the picture's border."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    rows, cols = lab.shape[:2]
    b = max(2, round(min(rows, cols) * 0.02))
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                             lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])  # fmt: skip
    background = np.median(border, axis=0)
    distance = np.linalg.norm(lab - background, axis=2)
    if distance.max() <= 0:
        return np.zeros(distance.shape, bool)
    scaled = (distance / distance.max() * 255).astype(np.uint8)
    otsu, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    noise = np.percentile(np.linalg.norm(border - background, axis=1), 95) / distance.max() * 255
    strokes = scaled > max(otsu, noise)
    size = max(3, round(max(rows, cols) * 0.01)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    strokes = cv2.morphologyEx(strokes.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    filled = ndimage.binary_fill_holes(strokes)
    core = cv2.erode(strokes.astype(np.uint8), kernel).astype(bool)
    if strokes.sum() < LINE_DRAWING * filled.sum() or not core.any():
        return filled  # a drawing of outlines (the subject is what they enclose), or too thin for GrabCut to refine
    marks = np.full(strokes.shape, cv2.GC_PR_BGD, np.uint8)
    marks[filled] = cv2.GC_PR_FGD
    marks[core] = cv2.GC_FGD
    for edge in (np.s_[:b], np.s_[-b:], np.s_[:, :b], np.s_[:, -b:]):
        marks[edge] = cv2.GC_BGD
    cv2.grabCut(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), marks, None, np.zeros((1, 65)), np.zeros((1, 65)), 5,
                cv2.GC_INIT_WITH_MASK)  # fmt: skip
    return (marks == cv2.GC_FGD) | (marks == cv2.GC_PR_FGD)


def _clean(mask: np.ndarray) -> np.ndarray:
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return np.zeros(mask.shape, bool)
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = 1 + np.flatnonzero(areas >= SPECKLE * areas.max())
    return np.isin(labels, keep)


# --------------------------------------------------------------------------- in the design's frame


@dataclass(frozen=True)
class Placed:
    """A picture's subject in the design's frame: its pixels `mm` wide, the picture's top-left corner at (left, top).
    Seen from a side (front, back, left, right), the subject stands on Z=0, centred across; from above or below, it
    is centred on the origin."""

    subject: Subject
    view: str
    mm: float
    left: float
    top: float

    @property
    def box(self) -> tuple[float, float, float, float]:
        """(left, bottom, right, top) of the subject, mm."""
        rows, cols = np.nonzero(self.subject.mask)
        return (self.left + cols.min() * self.mm, self.top - (rows.max() + 1) * self.mm,
                self.left + (cols.max() + 1) * self.mm, self.top - rows.min() * self.mm)  # fmt: skip


def place(subject: Subject, view: str, height: float) -> Placed:
    """The subject `height` mm from its bottom to its top in the picture."""
    rows, cols = np.nonzero(subject.mask)
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    mm = height / (r1 - r0)
    standing = VIEWS[view][1][0] == 2
    top = r1 * mm if standing else (r0 + r1) / 2 * mm
    return Placed(subject, view, mm, -(c0 + c1) / 2 * mm, top)


@dataclass(frozen=True)
class Grid:
    """Square cells `cell` mm wide, the top-left one's corner at (left, top)."""

    left: float
    top: float
    cell: float
    rows: int
    cols: int

    def px(self, hv: np.ndarray) -> np.ndarray:
        """(right, up) mm as (column, row) of the grid, cell centres at whole numbers."""
        return np.stack([(hv[:, 0] - self.left) / self.cell - 0.5, (self.top - hv[:, 1]) / self.cell - 0.5], axis=1)

    def mm(self, col: float, row: float) -> tuple[float, float]:
        return self.left + (col + 0.5) * self.cell, self.top - (row + 0.5) * self.cell

    def warp(
        self, image: np.ndarray, placed: Placed, interpolation: int, fill: int | tuple[int, ...] = 0
    ) -> np.ndarray:
        """A picture's pixels (`placed`) resampled onto the grid."""
        a = placed.mm / self.cell
        shift = [
            (placed.left - self.left) / self.cell + 0.5 * a - 0.5,
            (self.top - placed.top) / self.cell + 0.5 * a - 0.5,
        ]
        m = np.array([[a, 0, shift[0]], [0, a, shift[1]]], np.float32)
        return cv2.warpAffine(image, m, (self.cols, self.rows), flags=interpolation, borderValue=fill)


def covering(boxes: list[tuple[float, float, float, float]], finest: float) -> Grid:
    left, bottom = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, top = max(b[2] for b in boxes), max(b[3] for b in boxes)
    cell = max(finest, max(right - left, top - bottom) / MAX_CELLS)
    margin = 4 * cell
    return Grid(left - margin, top + margin, cell, math.ceil((top - bottom + 2 * margin) / cell),
                math.ceil((right - left + 2 * margin) / cell))  # fmt: skip


def fill_triangles(grid: Grid, hv: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """The cells a mesh covers, seen from the view `hv` was projected for; triangles one by one, as their union."""
    mask = np.zeros((grid.rows, grid.cols), np.uint8)
    corners = np.round(grid.px(hv) * 16).astype(np.int32)  # 4 fractional bits: `shift=4`
    for triangle in corners[faces]:
        cv2.fillConvexPoly(mask, triangle, 1, cv2.LINE_8, 4)
    return mask.astype(bool)


# --------------------------------------------------------------------------- comparing


@dataclass(frozen=True)
class Spot:
    """Where one outline strays furthest from the other, in one region of straying."""

    at: tuple[float, float]  # (right, up) mm: the stray's furthest point
    nearest: tuple[float, float]  # the other outline's nearest point to it
    depth: float  # mm apart
    beyond: bool  # the model reaches past the reference here; else it stops short of it


@dataclass(frozen=True)
class Comparison:
    grid: Grid
    model: np.ndarray
    reference: np.ndarray
    overlap: float  # intersection over union
    deviation: float  # mm: the furthest either strays from the other
    spots: list[Spot]  # the regions straying more than the tolerance, furthest first
    offset: tuple[float, float]  # (right, up) mm from the reference's box centre to the model's


def compare(placed: Placed, hv: np.ndarray, faces: np.ndarray, tolerance: float, most: int = 3) -> Comparison:
    """The model (`hv`, `faces`: its mesh projected with `plane`) against the picture's subject."""
    model_box = (*hv.min(axis=0), *hv.max(axis=0))
    grid = covering([placed.box, model_box], placed.mm)
    reference = grid.warp(placed.subject.mask.astype(np.float32), placed, cv2.INTER_LINEAR) > 0.5
    model = fill_triangles(grid, hv, faces)
    union = (model | reference).sum()
    overlap = float((model & reference).sum() / union) if union else 0.0
    spots: list[Spot] = []
    deviation = 0.0
    for beyond, inside, outside in ((True, model, reference), (False, reference, model)):
        stray = inside & ~outside
        if not stray.any():
            continue
        distance, (near_r, near_c) = ndimage.distance_transform_edt(~outside, return_indices=True)
        distance = np.where(stray, distance * grid.cell, 0.0)
        deviation = max(deviation, float(distance.max()))
        labels, n = ndimage.label(distance > tolerance, structure=np.ones((3, 3)))
        for r, c in ndimage.maximum_position(distance, labels, range(1, n + 1)) if n else []:
            spots.append(Spot(grid.mm(c, r), grid.mm(near_c[r, c], near_r[r, c]), float(distance[r, c]), beyond))
    spots.sort(key=lambda s: -s.depth)
    rb = placed.box
    offset = ((model_box[0] + model_box[2] - rb[0] - rb[2]) / 2, (model_box[1] + model_box[3] - rb[1] - rb[3]) / 2)
    return Comparison(grid, model, reference, overlap, deviation, spots[:most], offset)


def towards(view: str, dh: float, dv: float) -> str:
    """'+Z', or '+X, +Z': the design axes a direction in the picture's plane mostly runs along."""
    longest = max(abs(dh), abs(dv))
    parts = [axis(view, which, 1 if d > 0 else -1) for which, d in ((0, dh), (1, dv)) if abs(d) >= 0.4 * longest]
    return ", ".join(parts)


# --------------------------------------------------------------------------- the figure


def figure(placed: Placed, comparison: Comparison, title: str) -> bytes:
    """The picture faded on a millimetre grid in the design's axes, the reference's outline (blue), the model's
    (orange, shaded) and the spots where they part most, numbered as the result's message numbers them."""
    grid = comparison.grid
    scale = FIGURE_SIZE / max(grid.rows, grid.cols)
    size = (max(1, round(grid.cols * scale)), max(1, round(grid.rows * scale)))
    fine = Grid(grid.left, grid.top, grid.cell / scale, size[1], size[0])
    pixels = fine.warp(placed.subject.pixels, placed, cv2.INTER_AREA, (255, 255, 255))
    canvas = Image.fromarray((pixels * 0.45 + 255 * 0.55).astype(np.uint8)).convert("RGBA")
    shade = np.zeros((size[1], size[0], 4), np.uint8)
    shade[cv2.resize(comparison.model.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)] = (
        *MODEL,
        70,
    )
    canvas = Image.alpha_composite(canvas, Image.fromarray(shade))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=14)

    def to_canvas(h: float, v: float) -> tuple[float, float]:
        return (h - grid.left) / fine.cell, (grid.top - v) / fine.cell

    step = _nice((grid.cols * grid.cell) / 10)
    first_h, first_v = math.ceil(grid.left / step), math.ceil((grid.top - grid.rows * grid.cell) / step)
    for k in range(first_h, math.floor((grid.left + grid.cols * grid.cell) / step) + 1):
        x, _ = to_canvas(k * step, 0)
        draw.line([(x, 0), (x, size[1])], fill=(0, 0, 0, 90 if k == 0 else 35), width=1)
        draw.text((x + 2, size[1] - 16), f"{k * step:g}", fill=(60, 60, 60, 255), font=font)
    for k in range(first_v, math.floor(grid.top / step) + 1):
        _, y = to_canvas(0, k * step)
        draw.line([(0, y), (size[0], y)], fill=(0, 0, 0, 90 if k == 0 else 35), width=1)
        draw.text((2, y - 16), f"{k * step:g}", fill=(60, 60, 60, 255), font=font)
    for mask, colour in ((comparison.reference, REFERENCE), (comparison.model, MODEL)):
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            ring = [to_canvas(*grid.mm(c, r)) for c, r in contour[:, 0]]
            draw.line([*ring, ring[0]], fill=(*colour, 255), width=2)
    for n, spot in enumerate(comparison.spots, 1):
        a, b = to_canvas(*spot.at), to_canvas(*spot.nearest)
        draw.line([a, b], fill=(*STRAY, 255), width=2)
        draw.ellipse([a[0] - 9, a[1] - 9, a[0] + 9, a[1] + 9], outline=(*STRAY, 255), width=2)
        draw.text((a[0] + 11, a[1] - 9), str(n), fill=(*STRAY, 255), font=font)
    header = Image.new("RGBA", (size[0], 44), (255, 255, 255, 255))
    text = ImageDraw.Draw(header)
    text.text((8, 4), title, fill=(30, 30, 30, 255), font=font)
    x = 8.0
    for label, colour in ((f"grid {step:g} mm · right {axis(placed.view, 0)}, up {axis(placed.view, 1)} · ", (60, 60, 60)),
                          ("reference", REFERENCE), ("  model", MODEL), ("  where they part", STRAY)):  # fmt: skip
        text.text((x, 23), label, fill=(*colour, 255), font=font)
        x += text.textlength(label, font=font)
    sheet = Image.new("RGB", (size[0], size[1] + header.height), (255, 255, 255))
    sheet.paste(header.convert("RGB"), (0, 0))
    sheet.paste(canvas.convert("RGB"), (0, header.height))
    out = io.BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _nice(span: float) -> float:
    """1, 2 or 5 × 10^k: a grid step near `span`."""
    power = 10 ** math.floor(math.log10(span))
    return next(m * power for m in (1, 2, 5, 10) if m * power >= span)
