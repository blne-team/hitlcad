"""Building blocks part files import: `from hitlcad_lasercut.blocks import plate`.

Runs in the build process, with build123d.
"""

from __future__ import annotations

from collections.abc import Sequence

from build123d import Align, Box, BuildPart, Cylinder, Location, Locations, Mode, Part


def plate(width: float, depth: float, thickness: float, holes: Sequence[tuple[float, float]] = (),
          hole_diameter: float = 0.0) -> tuple[Part, dict[str, list[Location]]]:  # fmt: skip
    """A plate lying flat on Z = 0 (its own frame, as it is cut), `width` along X and `depth` along Y about the
    origin, with round through holes at `holes` (x, y): the part and its connectors, `{"holes": [...]}`, each
    hole's frame at its top opening with Z out of it, as hitlcad's hole connector reads it."""
    with BuildPart() as part:
        Box(width, depth, thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
        if holes:
            with Locations(*[(x, y, thickness / 2) for x, y in holes]):
                Cylinder(hole_diameter / 2, thickness, mode=Mode.SUBTRACT)
    return part.part, {"holes": [Location((x, y, thickness)) for x, y in holes]}
