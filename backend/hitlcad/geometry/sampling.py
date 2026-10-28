"""Local surface patches for sampling, refined only where the samples will be drawn."""

from __future__ import annotations

import math

import numpy as np
import trimesh


def surface_patch(mesh: trimesh.Trimesh, centre: np.ndarray, radius: float) -> tuple[trimesh.Trimesh, np.ndarray]:
    """A patch to sample within `radius` of `centre`, and each triangle's original face index.

    Bisect edges longer than the radius. Retain triangle centres within radius + radius/√3, the
    wall sampler's existing margin around its sampling ball. Before each bisection, discard
    triangles whose boxes cannot reach that region: refining a long, narrow face in full can
    otherwise create millions of triangles just to sample a small area of it. No adjacency is needed.
    """
    middle = mesh.triangles_center
    extent = np.linalg.norm(mesh.triangles - middle[:, None], axis=2).max(axis=1)
    source = np.flatnonzero(np.linalg.norm(middle - centre, axis=1) <= radius + extent)
    triangles = mesh.triangles[source]
    reach = radius * (1 + 1 / math.sqrt(3))
    while True:
        gap = np.maximum(np.maximum(triangles.min(axis=1) - centre, centre - triangles.max(axis=1)), 0)
        near = np.linalg.norm(gap, axis=1) <= reach
        triangles, source = triangles[near], source[near]
        split = np.linalg.norm(triangles - np.roll(triangles, -1, axis=1), axis=2) > radius
        if not split.any():
            break
        triangles, source = _bisect(triangles, source, split)
    keep = np.linalg.norm(triangles.mean(axis=1) - centre, axis=1) <= reach
    triangles, source = triangles[keep], source[keep]
    # Sampling reads areas and barycentric coordinates; welding vertices and building topology add no value.
    patch = trimesh.Trimesh(triangles.reshape(-1, 3), np.arange(3 * len(triangles)).reshape(-1, 3), process=False)
    return patch, source


def _bisect(triangles: np.ndarray, source: np.ndarray, split: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bisect the marked edges, preserving winding, source faces and a stable order of the split cases."""
    mid = (triangles + np.roll(triangles, -1, axis=1)) / 2
    count = split.sum(axis=1)
    out, index = [triangles[count == 0]], [source[count == 0]]

    # One long edge: rotate it to a-b and join its midpoint p to c.
    one = count == 1
    j = np.argmax(split[one], axis=1)
    rows = np.arange(len(j))
    t, m = triangles[one], mid[one]
    a, b, c = t[rows, j], t[rows, (j + 1) % 3], t[rows, (j + 2) % 3]
    p = m[rows, j]
    out.append(np.stack([a, p, c, p, b, c], axis=1).reshape(-1, 3, 3))
    index.append(np.repeat(source[one], 2))

    # Two long edges: rotate the short edge to c-a; split the remaining quad on its shorter diagonal.
    two = count == 2
    j = (np.argmin(split[two], axis=1) + 1) % 3
    rows = np.arange(len(j))
    t, m = triangles[two], mid[two]
    a, b, c = t[rows, j], t[rows, (j + 1) % 3], t[rows, (j + 2) % 3]
    p, q = m[rows, j], m[rows, (j + 1) % 3]
    use_aq = (np.sum((a - q) ** 2, axis=1) <= np.sum((p - c) ** 2, axis=1))[:, None, None]
    out.extend(
        [
            np.stack([p, b, q], axis=1),
            np.where(use_aq, np.stack([a, p, q], axis=1), np.stack([a, p, c], axis=1)),
            np.where(use_aq, np.stack([a, q, c], axis=1), np.stack([p, q, c], axis=1)),
        ]
    )
    index.extend([source[two]] * 3)

    # Three long edges: the four triangles of the regular midpoint split.
    three = count == 3
    a, b, c = triangles[three].transpose(1, 0, 2)
    p, q, r = mid[three].transpose(1, 0, 2)
    out.append(np.stack([a, p, r, p, b, q, r, q, c, p, q, r], axis=1).reshape(-1, 3, 3))
    index.append(np.repeat(source[three], 4))
    return np.concatenate(out), np.concatenate(index)
