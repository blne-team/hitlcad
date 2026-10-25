"""A part's features measured on its B-rep and on its mesh: the two adapters agree, the mesh to within the
uncertainty it reports, on the features that are easy to get wrong."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh
from build123d import Align, Box, BuildPart, Cone, Cylinder, Locations, Mode, PolarLocations, export_stl

from hitlcad.analysis.features import BrepFeatures, MeshFeatures
from hitlcad.analysis.mesh import load_mesh
from hitlcad.core import frames as fr
from hitlcad.core.structure import Tolerances

pytestmark = pytest.mark.geometry
ON = (Align.CENTER, Align.CENTER, Align.MIN)
TOL = Tolerances(position_mm=0.01, angle_deg=0.05, motion_steps=2000)


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
    return both(part.part, tmp_path_factory)


def both(shape: object, tmp_path_factory: pytest.TempPathFactory) -> tuple[BrepFeatures, MeshFeatures]:
    """A part's two adapters: on its B-rep, and on its mesh as STL carries it."""
    stl = Path(tmp_path_factory.mktemp("part")) / "part.stl"
    export_stl(shape, str(stl), tolerance=0.01, angular_tolerance=0.1)
    mesh = load_mesh(stl)
    return BrepFeatures("part", shape, lambda: mesh, list), MeshFeatures("part", lambda: mesh, list)


@pytest.fixture(scope="module")
def placed(tmp_path_factory: pytest.TempPathFactory) -> tuple[BrepFeatures, MeshFeatures]:
    """Features to place frames by: a plain hole, a countersunk one, a half-round notch at the edge, a pin."""
    with BuildPart() as part:
        Box(60, 20, 10, align=ON)
        with Locations((-20, 0, 0)):  # Ø5 through
            Cylinder(2.5, 10, align=ON, mode=Mode.SUBTRACT)
        with Locations((0, 0, 8)):  # a 90° countersink 2 deep, then Ø3.4 through
            Cone(1.7, 3.7, 2, align=ON, mode=Mode.SUBTRACT)
        with Locations((0, 0, 0)):
            Cylinder(1.7, 10, align=ON, mode=Mode.SUBTRACT)
        with Locations((30, 0, 0)):  # half a Ø5 hole, at the edge
            Cylinder(2.5, 10, align=ON, mode=Mode.SUBTRACT)
        with Locations((20, 0, 10)):  # a Ø8 pin, 6 tall
            Cylinder(4, 6, align=ON)
    return both(part.part, tmp_path_factory)


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


PLACED = [
    ("5 mm above a hole", at(-20, 0, 15), True, "nearest: Ø5.000 hole, axis 0.000 mm off, 0.00° tilt, starts 5.00 mm below "
     "the origin with no coaxial surface leading to it"),
    ("0.02 mm off a hole's axis", at(-19.98, 0, 10), True, "nearest: Ø5.000 hole, axis 0.020 mm off (allowed 0.01), "
     "0.00° tilt"),
    ("3 mm below a pin's shoulder", at(20, 0, 7), False, "nearest: Ø8.000 shaft, axis 0.000 mm off, 0.00° tilt, starts "
     "3.00 mm above the origin with no coaxial surface leading to it"),
    ("at a half-round notch", at(30, 0, 10), True, "no hole on the frame's axis below its origin"),
]  # fmt: skip


@pytest.mark.parametrize(("what", "frame", "inward", "says"), PLACED, ids=[p[0] for p in PLACED])
def test_a_frame_away_from_its_feature_fails_alike_on_b_rep_and_mesh(
    placed: tuple[BrepFeatures, MeshFeatures], what: str, frame: fr.Frame, inward: bool, says: str
) -> None:
    for features in placed:
        measured = features.round(frame, TOL, inward=inward)
        assert measured.status == "fail" and says in measured.detail, (features.representation, measured.detail)
    assert "goes 180° round, not a full circle" in placed[0].round(at(30, 0, 10), TOL, inward=True).detail


def test_coaxial_surfaces_lead_from_the_origin_to_its_feature(placed: tuple[BrepFeatures, MeshFeatures]) -> None:
    for features in placed:  # a screw's clearance frame on the face: through its countersink to the hole
        countersunk = features.round(at(0, 0, 10), TOL, inward=True)
        assert countersunk.status == "pass" and countersunk.values["diameter"] == pytest.approx(3.4, abs=0.02)
        assert "starting 2.00 mm from the origin" in countersunk.detail


def test_a_mesh_round_is_the_same_however_the_frame_turns() -> None:
    plate = trimesh.creation.box((30, 30, 10)).difference(trimesh.creation.cylinder(radius=5, height=12, sections=32))
    pin = trimesh.creation.cylinder(radius=5, height=10, sections=32)
    sagitta = 5 * (1 - np.cos(np.pi / 32))
    for mesh, origin, inward in ((plate, (0, 0, 5), True), (pin, (0, 0, -5), False)):
        features = MeshFeatures("part", lambda mesh=mesh: mesh, list)
        for turn in (0.0, 180 / 32, 21.2):
            frame = fr.translation(origin) @ fr.rotation_about((0, 0, 0), (0, 0, 1), turn)
            m = features.round(frame, TOL, inward=inward)
            assert m.values["diameter"] == pytest.approx(10, abs=1e-6), (turn, m.detail)
            assert m.uncertainty_mm == pytest.approx(2 * sagitta, rel=1e-3)


@pytest.fixture(scope="module")
def gear(tmp_path_factory: pytest.TempPathFactory) -> tuple[BrepFeatures, MeshFeatures]:
    """A spur gear's outline in the large: m2, 12 teeth from the Ø19 root circle to the Ø28 tip, 5 wide."""
    with BuildPart() as part:
        Cylinder(9.5, 5, align=ON)
        with PolarLocations(12, 12):
            Box(5, 3, 5, align=ON)
        Cylinder(14, 5, align=ON, mode=Mode.INTERSECT)
    return both(part.part, tmp_path_factory)


def test_a_gear_measures_its_tip_and_teeth_across_its_face_width(gear: tuple[BrepFeatures, MeshFeatures]) -> None:
    for features in gear:
        ok = features.gear(at(0, 0, 0), TOL, module=2, teeth=12, width=5, internal=False)
        assert ok.status == "pass" and ok.values["teeth"] == 12, ok.detail
        assert ok.values["tip_diameter"] == pytest.approx(28, abs=0.01)
        bigger = features.gear(at(0, 0, 0), TOL, module=2.5, teeth=12, width=5, internal=False)
        assert bigger.status == "fail" and "tip Ø28.000, m2.5 × (12 + 2) needs Ø35.000" in bigger.detail
        more = features.gear(at(0, 0, 0), TOL, module=2, teeth=13, width=5, internal=False)
        assert more.status == "fail" and "12 teeth across the face width, 13 declared" in more.detail
        upside_down = features.gear(at(0, 0, 5), TOL, module=2, teeth=12, width=5, internal=False)
        assert upside_down.status == "fail" and "no external gear teeth" in upside_down.detail


def test_a_hole_split_into_half_faces_is_one_hole() -> None:
    """CAD exporters often write a hole's wall as two half-faces, on trimmed surfaces: still one hole, no corner."""
    from build123d import Shape
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed

    from hitlcad.analysis.geometry import face_text

    divide = ShapeUpgrade_ShapeDivideClosed((Box(30, 30, 10, align=ON) - Cylinder(1.5, 10, align=ON)).wrapped)
    divide.SetNbSplitPoints(1)
    divide.Perform()
    shape = Shape.cast(divide.Result())
    halves = [f for f in shape.faces() if f.geom_type.name == "CYLINDER"]
    assert len(halves) == 2 and halves[0].radius is None  # build123d gives a trimmed surface no radius
    features = BrepFeatures("part", shape, lambda: None, list)  # type: ignore[arg-type, return-value]
    assert [(h.diameter, h.depth) for h in features.holes()] == [(pytest.approx(3), pytest.approx(10))]
    assert features.inside_corners(TOL) == []
    assert features.round(at(0, 0, 10), TOL, inward=True).values["diameter"] == pytest.approx(3)
    assert "r=1.50 mm" in face_text(halves[0], {})
