"""Local refinement preserves the sampled patch without refining the entire source surface."""

from __future__ import annotations

import math

import numpy as np
import pytest
import trimesh

from hitlcad.geometry import sampling


def full_patch(mesh: trimesh.Trimesh, centre: np.ndarray, radius: float) -> tuple[trimesh.Trimesh, np.ndarray]:
    """The original sampling region: refine all nearby faces, then discard the distant triangles."""
    middle = mesh.triangles_center
    extent = np.linalg.norm(mesh.triangles - middle[:, None], axis=2).max(axis=1)
    near = np.flatnonzero(np.linalg.norm(middle - centre, axis=1) <= radius + extent)
    vertices, faces, source = trimesh.remesh.subdivide_to_size(
        mesh.vertices, mesh.faces[near], radius, return_index=True
    )
    patch = trimesh.Trimesh(vertices, faces, process=False)
    keep = np.flatnonzero(np.linalg.norm(patch.triangles_center - centre, axis=1) <= radius * (1 + 1 / math.sqrt(3)))
    return patch.submesh([keep], append=True), near[source[keep]]


@pytest.mark.parametrize(
    "mesh,centre,radius",
    [
        (trimesh.creation.box((80, 2, 2)), np.array([0, 1, 0]), 1.0),
        (trimesh.creation.cylinder(3, 40, sections=24), np.array([3, 0, 0]), 2.0),
        (trimesh.creation.icosphere(subdivisions=2), np.array([0, 0, 1]), 0.4),
        # Exercise short, one-long-edge, two-long-edge and three-long-edge triangles together.
        (
            trimesh.Trimesh(
                [[0, 0, 0], [0.5, 0, 0], [0, 0.5, 0], [1.2, 0, 0], [0.6, 0.7, 0], [3, 0, 0], [0, 3, 0]],
                [[0, 1, 2], [0, 3, 4], [0, 1, 5], [0, 5, 6]],
                process=False,
            ),
            np.array([0.1, 0.1, 0]),
            1.0,
        ),
    ],
)
def test_local_patch_preserves_faces_and_samples(mesh: trimesh.Trimesh, centre: np.ndarray, radius: float) -> None:
    expected, faces = full_patch(mesh, centre, radius)
    actual, source = sampling.surface_patch(mesh, centre, radius)
    np.testing.assert_array_equal(source, faces)
    np.testing.assert_array_equal(actual.triangles, expected.triangles)
    np.testing.assert_allclose(actual.face_normals, mesh.face_normals[source], atol=1e-12)
    np.testing.assert_array_equal(
        trimesh.sample.sample_surface_even(actual, 200, seed=0)[0],
        trimesh.sample.sample_surface_even(expected, 200, seed=0)[0],
    )


def test_local_refinement_does_not_expand_a_long_face_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refining the middle of a million-to-one face does not expand its distant ends."""
    mesh = trimesh.Trimesh([[-1e6, 0, 0], [1e6, 0, 0], [1e6, 1, 0]], [[0, 1, 2]], process=False)
    working_sets = []
    bisect = sampling._bisect

    def measured(triangles: np.ndarray, source: np.ndarray, split: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        working_sets.append(len(triangles))
        return bisect(triangles, source, split)

    monkeypatch.setattr(sampling, "_bisect", measured)
    patch, source = sampling.surface_patch(mesh, np.array([0, 0.25, 0]), 1.0)
    assert max(working_sets) < 256
    assert len(patch.faces) < 256 and patch.area > 1
    assert (source == 0).all()
    assert np.linalg.norm(patch.triangles - np.roll(patch.triangles, -1, axis=1), axis=2).max() <= 1
