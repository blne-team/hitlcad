"""What of an assembly shows: the faces seen from outside it.

Seen from each of `DIRECTIONS` (evenly round a sphere), parallel rays (at most `RAYS` across its outline, no closer
than `SPACING_MM`) meet it: how near each meets it is a depth image. A ball of `BALL_MM` rolled over that image (a
grey closing by a sphere) touches what a view from that side sees; it does not pass through an opening narrower than it (a vent, a grille, a
finger guard), so what lies behind one does not show. The first face each ray meets shows where the ball touches
it there. A face against another part, or inside the product, shows from no side. A face smaller than the rays'
spacing may be missed, so what shows is a lower bound by about that much."""

from __future__ import annotations

import numpy as np
import trimesh
from scipy import ndimage

from hitlcad.geometry.mesh import Probe

#: Seen from these: an icosphere's vertices, 42 directions about 30° apart.
DIRECTIONS = trimesh.creation.icosphere(subdivisions=1).vertices
#: Rays from each direction, across the assembly's outline seen from it, at most.
RAYS = 40_000
#: Rays no closer than this: about a nozzle's line, finer than any print shows a face.
SPACING_MM = 0.5
#: An opening narrower than twice this hides what lies behind it.
BALL_MM = 5.0


def shown(meshes: list[trimesh.Trimesh]) -> list[np.ndarray]:
    """Of each mesh, placed together, the faces seen from outside all of them: a boolean per face."""
    if not meshes:
        return []
    whole = trimesh.util.concatenate(meshes)
    probe = Probe(whole)
    seen = np.zeros(len(whole.faces), dtype=bool)
    for d in DIRECTIONS:
        u = np.cross(d, (1.0, 0.0, 0.0) if abs(d[0]) < 0.9 else (0.0, 1.0, 0.0))
        u /= np.linalg.norm(u)
        v = np.cross(d, u)
        across = whole.vertices @ np.column_stack([u, v])
        lo, hi = across.min(axis=0), across.max(axis=0)
        spacing = max(float(np.sqrt(np.prod(hi - lo) / RAYS)), SPACING_MM)
        a, b = (np.arange(lo[k] + spacing / 2, hi[k], spacing) for k in (0, 1))
        grid = np.stack(np.meshgrid(a, b), axis=-1)
        behind = float((whole.vertices @ d).min()) - spacing
        origins = grid[..., :1] * u + grid[..., 1:] * v + behind * d
        face, t = probe.first_hit(origins.reshape(-1, 3), np.broadcast_to(d, (origins.size // 3, 3)).copy())
        face, t = face.reshape(grid.shape[:2]), t.reshape(grid.shape[:2])
        hit = face >= 0
        if not hit.any():
            continue
        # nearness: what the ball rolls on; nothing met lies far behind everything met
        near = np.where(hit, -t, -float(t[hit].max()) - 4 * BALL_MM)
        touched = near >= _rolled(near, spacing) - spacing
        seen[face[hit & touched]] = True
    return np.split(seen, np.cumsum([len(m.faces) for m in meshes])[:-1])


def _rolled(near: np.ndarray, spacing: float) -> np.ndarray:
    """The nearness a ball of `BALL_MM` rolled over the image `near` (pixels `spacing` apart) touches: its grey
    closing by the ball."""
    k = int(np.ceil(BALL_MM / spacing))
    dy, dx = np.mgrid[-k : k + 1, -k : k + 1] * spacing
    inside = dx * dx + dy * dy <= BALL_MM * BALL_MM
    cap = np.sqrt(np.clip(BALL_MM * BALL_MM - dx * dx - dy * dy, 0.0, None))
    return ndimage.grey_closing(near, footprint=inside, structure=np.where(inside, cap - BALL_MM, 0.0))
