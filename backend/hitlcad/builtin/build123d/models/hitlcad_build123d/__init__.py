"""Reusable build123d construction for hitlcad part files. Dimensions are millimetres, Z up."""

from __future__ import annotations

import math
from collections.abc import Sequence

from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Mode, Part, Pos

__all__ = ["mounting_plate"]


def mounting_plate(
    *,
    width: float,
    depth: float,
    thickness: float,
    holes: Sequence[Sequence[float]],
    hole_diameter: float,
    boss_height: float = 0,
    boss_diameter: float | None = None,
) -> tuple[Part, dict[str, Location | list[Location]]]:
    """Build a centered rectangular plate, through holes and optional bosses above its top.

    Args:
        width: X extent, positive mm.
        depth: Y extent, positive mm.
        thickness: Plate thickness from Z=0, positive mm.
        holes: Nonempty sequence of (x, y) hole centers, in the plate's frame.
        hole_diameter: Round through-hole diameter, positive mm; no thread or fit allowance is implied.
        boss_height: Additional height above the plate, nonnegative mm.
        boss_diameter: Required when boss_height > 0; larger than hole_diameter.

    Returns:
        (shape, connectors), suitable as a part's value in build(p, i). `bottom` faces -Z at Z=0,
        `top` faces +Z at Z=thickness, and `holes` face +Z at each hole's upper opening, in input order.

    Raises:
        ValueError: Dimensions are nonfinite, holes overlap or break through the plate's sides,
            or a boss has no wall. The message names the argument or hole to change.
    """
    for name, value in (("width", width), ("depth", depth), ("thickness", thickness), ("hole_diameter", hole_diameter)):
        _positive(name, value)
    if not math.isfinite(boss_height) or boss_height < 0:
        raise ValueError(f"boss_height must be finite and >= 0 mm; got {boss_height!r}")
    if boss_diameter is not None:
        _positive("boss_diameter", boss_diameter)
        if boss_diameter <= hole_diameter:
            raise ValueError("boss_diameter must exceed hole_diameter so each boss retains a wall")
    if boss_height > 0 and boss_diameter is None:
        raise ValueError("boss_diameter is required when boss_height > 0")
    centers = []
    radius = (boss_diameter if boss_height > 0 else hole_diameter) / 2
    for index, point in enumerate(holes):
        if len(point) != 2 or not all(math.isfinite(v) for v in point):
            raise ValueError(f"holes[{index}] must be a finite (x, y) pair in mm; got {point!r}")
        x, y = point
        if abs(x) + radius >= width / 2 or abs(y) + radius >= depth / 2:
            raise ValueError(
                f"holes[{index}] at ({x:g}, {y:g}) leaves no plate border around its hole or boss; "
                "move it inward or increase width/depth"
            )
        if any(math.dist((x, y), previous) <= hole_diameter for previous in centers):
            raise ValueError(
                f"holes[{index}] touches or overlaps another hole; separate centers by more than hole_diameter"
            )
        centers.append((x, y))
    if not centers:
        raise ValueError("holes must contain at least one (x, y) center")

    on_bed = (Align.CENTER, Align.CENTER, Align.MIN)
    # PRIVATE keeps the helper independent of a caller's active BuildPart context.
    with BuildPart(mode=Mode.PRIVATE) as plate:
        Box(width, depth, thickness, align=on_bed)
        if boss_height > 0:
            with Locations(*[Pos(x, y, thickness) for x, y in centers]):
                Cylinder(boss_diameter / 2, boss_height, align=on_bed)
        with Locations(*[Pos(x, y, 0) for x, y in centers]):
            Cylinder(hole_diameter / 2, thickness + boss_height, align=on_bed, mode=Mode.SUBTRACT)
    return plate.part, {
        "bottom": Location((0, 0, 0), (180, 0, 0)),
        "top": Location((0, 0, thickness)),
        "holes": [Location((x, y, thickness + boss_height)) for x, y in centers],
    }


def _positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0 mm; got {value!r}")
