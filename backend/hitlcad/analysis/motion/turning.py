"""Turning, seen in profile: what of a solid is round about an axis, and what a solid passes through in a full turn
about it. What is round about the axis of a turn turns within itself and keeps its gap to anything, so a sweep moves
only the rest; a solid's full turn is the solid of revolution of its profile."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from hitlcad.core import frames as fr
from hitlcad.core.structure import Axis

if TYPE_CHECKING:
    import manifold3d


def axis_frame(axis: Axis) -> np.ndarray:
    """The axis' own frame (3 × 4, as manifold3d takes it): X and Y across it, Z along it, the origin on it."""
    return fr.on_axis(axis.origin, axis.direction)[:3]


#: The grid (mm) a profile's regions are moved out to where it is given no pad.
GRID_MM = 1e-6


def _local(solid: manifold3d.Manifold, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The solid's vertices in the axis' frame, and its triangles."""
    mesh = solid.to_mesh()
    return (np.asarray(mesh.vert_properties[:, :3], dtype=np.float64) - frame[:, 3]) @ frame[:, :3], mesh.tri_verts


def radius(solid: manifold3d.Manifold, frame: np.ndarray) -> float:
    """How far the solid reaches from the axis."""
    vertices, _ = _local(solid, frame)
    return float(np.linalg.norm(vertices[:, :2], axis=1).max()) if len(vertices) else 0.0


def profile(solid: manifold3d.Manifold, frame: np.ndarray, pad: float) -> manifold3d.CrossSection:
    """In the plane of distance from the axis (X) and position along it (Y), the region each of the solid's
    triangles spans: a little more than its surface passes through in a turn. Each region's edges are moved out
    to a grid of `pad` (of `GRID_MM` where `pad` is 0), so a triangle square to the axis, which turns within its
    own plane, spans one step of it; the triangles of a ring about the axis then span one region, the same one, and
    the regions are joined once each."""
    import manifold3d

    vertices, faces = _local(solid, frame)
    triangles = vertices[faces]
    outer, inner = np.linalg.norm(triangles[..., :2], axis=-1).max(axis=1), _distance_to_origin(triangles[..., :2])
    lo, hi = triangles[..., 2].min(axis=1) - pad, triangles[..., 2].max(axis=1) + pad
    grid = pad or GRID_MM
    spans = np.unique(np.column_stack([np.floor(inner / grid), np.ceil(outer / grid), np.floor(lo / grid),
                                       np.ceil(hi / grid)]), axis=0) * grid  # fmt: skip
    inner, outer, lo, hi = spans.T
    rects = np.stack([np.stack(c, axis=-1) for c in ((inner, lo), (outer, lo), (outer, hi), (inner, hi))], axis=1)
    return manifold3d.CrossSection(list(rects), manifold3d.FillRule.Positive)


def revolve(outline: manifold3d.CrossSection, frame: np.ndarray, tolerance: float) -> manifold3d.Manifold:
    """A profile turned about the axis, as a polygon around each circle (not in it), no more than `tolerance`
    outside it."""
    import manifold3d

    far = max(outline.bounds()[2], tolerance)
    segments = int(min(1024, max(16, math.ceil(math.pi * math.sqrt(far / (2 * tolerance))))))
    widened = outline.scale((1 / math.cos(math.pi / segments), 1.0))
    return manifold3d.Manifold.revolve(widened, segments).transform(frame)


def not_round(solid: manifold3d.Manifold, frame: np.ndarray, tolerance: float) -> manifold3d.Manifold:
    """The solid less its part that is round about the axis, to `tolerance` (a facetted round is round): all a
    turn about the axis moves. Its triangles' profile (`profile`) holds every circle its surface crosses,
    so each region of the profile plane it leaves out holds circles all inside the solid or all outside;
    the regions inside, turned about the axis, are its round part."""
    import manifold3d

    outline = profile(solid, frame, tolerance / 10)
    x0, y0, x1, y1 = outline.bounds()
    box = manifold3d.CrossSection.square((x1 + 1.0, y1 - y0 + 2.0)).translate((0.0, y0 - 1.0))
    inside = []
    for region in (box - outline).decompose():
        turned = manifold3d.Manifold.revolve(region, 32).transform(frame)
        if (turned ^ solid).volume() > turned.volume() / 2:
            inside.append(region)
    if not inside:
        return solid
    core = manifold3d.CrossSection.batch_boolean(inside, manifold3d.OpType.Add).offset(
        tolerance, manifold3d.JoinType.Miter
    )
    return solid - revolve(core, frame, tolerance)


def _distance_to_origin(triangles: np.ndarray) -> np.ndarray:
    """Each 2D triangle's (n × 3 × 2) distance from the origin; 0 for one that covers it."""
    a, ab = triangles, np.roll(triangles, -1, axis=1) - triangles
    t = np.clip(-np.einsum("ijk,ijk->ij", a, ab) / np.maximum(np.einsum("ijk,ijk->ij", ab, ab), 1e-30), 0.0, 1.0)
    edges = np.linalg.norm(a + t[..., None] * ab, axis=-1).min(axis=1)
    turn = ab[..., 0] * -a[..., 1] - ab[..., 1] * -a[..., 0]  # which side of each edge the origin is on
    covers = np.all(turn > 0, axis=1) | np.all(turn < 0, axis=1)  # strictly: one seen edge-on covers nothing
    return np.where(covers, 0.0, edges)
