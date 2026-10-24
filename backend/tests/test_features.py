"""A part's features measured on its B-rep and on its mesh: the two adapters agree, the mesh to within the
uncertainty it reports, on the features that are easy to get wrong."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from build123d import Align, Box, BuildPart, Cylinder, Locations, Mode, export_stl

from hitlcad.analysis.features import BrepFeatures, MeshFeatures
from hitlcad.analysis.mesh import load_mesh
from hitlcad.core import frames as fr
from hitlcad.core.structure import Tolerances

pytestmark = pytest.mark.geometry
ON = (Align.CENTER, Align.CENTER, Align.MIN)
TOL = Tolerances(position_mm=0.01, angle_deg=0.05, motion_samples=8)


def at(x: float, y: float, z: float, down: bool = False) -> fr.Frame:
    """A frame at (x, y, z) with Z up, or down (a hole opening downwards, a shaft pointing down)."""
    flip = fr.flip_x() if down else fr.IDENTITY
    return fr.translation((x, y, z)) @ flip


@pytest.fixture(scope="module")
def adapters(tmp_path_factory: pytest.TempPathFactory) -> tuple[BrepFeatures, MeshFeatures]:
    with BuildPart() as part:
        Box(80, 40, 10, align=ON)
        with Locations((-25, 0, 10)):  # a counterbored hole: Ø6 × 2, then Ø3.4 through
            Cylinder(3, 2, align=(Align.CENTER, Align.CENTER, Align.MAX), mode=Mode.SUBTRACT)
            Cylinder(1.7, 10, align=(Align.CENTER, Align.CENTER, Align.MAX), mode=Mode.SUBTRACT)
        with Locations((0, 0, 10)):  # a hollow spigot: Ø12 outside, 6 long, and a Ø8 bore through it all
            Cylinder(6, 6, align=ON)
        with Locations((0, 0, 0)):
            Cylinder(4, 16, align=ON, mode=Mode.SUBTRACT)
        with Locations((25, 0, 10)):  # a slot through a hole's wall: not a full circle
            Cylinder(2.5, 6, align=(Align.CENTER, Align.CENTER, Align.MAX), mode=Mode.SUBTRACT)
            Box(8, 1.5, 3, align=(Align.MIN, Align.CENTER, Align.MAX), mode=Mode.SUBTRACT)
        with Locations((-25, 15, 0), (-25, 15, 10)):  # two Ø2 holes on one axis, one from each face, 3 deep
            Cylinder(1, 6, mode=Mode.SUBTRACT)
    stl = Path(tmp_path_factory.mktemp("part")) / "part.stl"
    export_stl(part.part, str(stl), tolerance=0.01, angular_tolerance=0.1)
    return BrepFeatures(part.part), MeshFeatures(load_mesh(stl), "part")


ROUND = [
    ("counterbore", at(-25, 0, 10), True, {"diameter": 6, "length": 2, "offset": 0}),
    ("through its counterbore", at(-25, 0, 8), True, {"diameter": 3.4, "length": 8, "offset": 0}),
    ("hollow spigot", at(0, 0, 10), False, {"diameter": 12, "length": 6, "offset": 0}),
    ("hollow spigot's bore", at(0, 0, 16), True, {"diameter": 8, "length": 16, "offset": 0}),
    ("hole with a slot through its wall", at(25, 0, 10), True, {"diameter": 5, "length": 6, "offset": 0}),
    ("upper of two coaxial holes", at(-25, 15, 10), True, {"diameter": 2, "length": 3, "offset": 0}),
    ("lower of two coaxial holes", at(-25, 15, 0, down=True), True, {"diameter": 2, "length": 3, "offset": 0}),
]


@pytest.mark.parametrize(("what", "frame", "inward", "expected"), ROUND, ids=[r[0] for r in ROUND])
def test_round_features_measure_alike_on_b_rep_and_mesh(
    adapters: tuple[BrepFeatures, MeshFeatures], what: str, frame: fr.Frame, inward: bool, expected: dict[str, float]
) -> None:
    brep, mesh = adapters
    exact, faceted = brep.round(frame, TOL, inward=inward), mesh.round(frame, TOL, inward=inward)
    assert exact.status == "pass" and faceted.status == "pass", (exact.detail, faceted.detail)
    for key, value in expected.items():
        assert exact.values[key] == pytest.approx(value, abs=TOL.position_mm), (key, exact.detail)
        assert faceted.values[key] == pytest.approx(value, abs=0.02 + faceted.uncertainty_mm), (key, faceted.detail)


def test_a_frame_off_any_feature_fails_on_both(adapters: tuple[BrepFeatures, MeshFeatures]) -> None:
    for features in adapters:
        assert features.round(at(10, 10, 10), TOL, inward=True).status == "fail"
        assert features.face(at(10, 10, 5), TOL).status == "fail"  # inside the part, not on a face


def test_faces_measure_alike_and_an_annulus_counts_its_centre(adapters: tuple[BrepFeatures, MeshFeatures]) -> None:
    brep, mesh = adapters
    for frame in (at(30, -15, 10), at(0, 0, 16), at(0, 0, 0, down=True)):  # the top; the spigot's rim, at its centre
        exact, faceted = brep.face(frame, TOL), mesh.face(frame, TOL)
        assert exact.status == faceted.status == "pass", (exact.detail, faceted.detail)
        assert np.isclose(exact.values["area"], faceted.values["area"], rtol=1e-3)
