"""Reusable Blender construction for hitlcad part files: stdlib, bpy and mathutils only."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bpy.types import Object
    from mathutils import Matrix

__all__ = ["section_form"]


def section_form(
    *,
    sections: Sequence[Sequence[float]],
    wall: float,
    floor: float | None = None,
    chord_tolerance: float = 0.05,
    name: str = "form",
) -> tuple[Object, dict[str, Matrix]]:
    """Build a hollow rotational form with a floor and open mouth, using mm and Z up.

    Args:
        sections: At least two (z, outer_radius) pairs, starting at Z=0, with strictly increasing Z.
            The silhouette is piecewise linear between these sections.
        wall: Positive radial offset, smaller than every outer radius. This is NOT normal wall thickness.
        floor: Positive floor height below the last section; defaults to wall.
        chord_tolerance: Maximum radial faceting error at the largest ring, positive mm. Axial
            section spacing is controlled separately by sections. Very small tolerances are rejected.
        name: Object and mesh name for picks in the framework's preview.

    Returns:
        (object, connectors), suitable as a part's value in build(p, i). `bottom` faces -Z at Z=0;
        `rim` faces +Z at the mouth's center. These are datum frames, not verified planar faces.

    Raises:
        ValueError: A section, thickness or resolution is invalid. No scene objects are created until
            inputs have been validated. Use the framework's wall check for actual local thickness.
    """
    for argument, value in (("wall", wall), ("chord_tolerance", chord_tolerance)):
        _positive(argument, value)
    floor = wall if floor is None else floor
    _positive("floor", floor)
    rings = []
    for index, section in enumerate(sections):
        if len(section) != 2 or not all(math.isfinite(v) for v in section):
            raise ValueError(f"sections[{index}] must be a finite (z, outer_radius) pair in mm; got {section!r}")
        z, radius = section
        if (not rings and z != 0) or (rings and z <= rings[-1][0]):
            raise ValueError(f"sections[{index}].z must start at 0 and strictly increase; got {z:g}")
        if radius <= wall:
            raise ValueError(f"sections[{index}].outer_radius ({radius:g} mm) must exceed wall ({wall:g} mm)")
        rings.append((z, radius))
    if len(rings) < 2:
        raise ValueError("sections must contain at least two (z, outer_radius) pairs")
    height = rings[-1][0]
    if floor >= height:
        raise ValueError(f"floor ({floor:g} mm) must be below the final section ({height:g} mm)")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a nonempty string")
    segments = _segments(max(radius for _, radius in rings), chord_tolerance)

    # Interpolate the outer profile at the inner floor, so both walls follow the same silhouette.
    heights = [z for z, _ in rings]
    upper = bisect_right(heights, floor)
    za, ra = rings[upper - 1]
    zb, rb = rings[upper]
    floor_radius = ra + (rb - ra) * (floor - za) / (zb - za)
    inner = [(floor, floor_radius - wall), *((z, r - wall) for z, r in rings if z > floor)]
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []

    def surface(profile: list[tuple[float, float]], *, inside: bool) -> list[int]:
        starts = []
        for z, radius in profile:
            starts.append(len(vertices))
            for segment in range(segments):
                angle = 2 * math.pi * segment / segments
                vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
        for low, high in zip(starts, starts[1:], strict=False):
            for j in range(segments):
                k = (j + 1) % segments
                face = (low + j, low + k, high + k, high + j)
                faces.append(tuple(reversed(face)) if inside else face)
        return starts

    outer_starts = surface(rings, inside=False)
    inner_starts = surface(inner, inside=True)
    faces.append(tuple(reversed(range(segments))))
    faces.append(tuple(inner_starts[0] + j for j in range(segments)))
    outer_top, inner_top = outer_starts[-1], inner_starts[-1]
    for j in range(segments):
        k = (j + 1) % segments
        faces.append((outer_top + j, outer_top + k, inner_top + k, inner_top + j))

    import bpy
    from mathutils import Matrix

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, {
        "bottom": Matrix.Rotation(math.pi, 4, "X"),
        "rim": Matrix.Translation((0, 0, height)),
    }


def _positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0 mm; got {value!r}")


def _segments(radius: float, tolerance: float) -> int:
    """A multiple of four keeps the cardinal axes on vertices and the envelope exact."""
    angle = math.acos(max(0.0, 1 - tolerance / radius))
    if angle == 0 or math.pi / angle > 4096:
        raise ValueError("chord_tolerance needs more than 4096 ring segments; increase it or reduce the radius")
    return max(12, 4 * math.ceil(math.pi / angle / 4))
