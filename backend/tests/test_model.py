"""The domain model on its own: addresses, design.json validation, mate and relation geometry, fits, settings."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from hitlcad import materials
from hitlcad.analysis.iso286 import fit_clearance
from hitlcad.core import frames as fr
from hitlcad.core.address import End, InstancePath, parse_mate
from hitlcad.core.design import DesignSpec
from hitlcad.core.structure import (
    Axis,
    CylindricalMate,
    Fit,
    GearRelation,
    RackRelation,
    RigidMate,
    ScrewRelation,
    Tolerances,
    Verification,
    symmetry_of,
)

TOL = Tolerances(position_mm=0.01, angle_deg=0.05, motion_samples=8)


def frame(origin=(0, 0, 0), z_down=False, turn_deg=0.0) -> fr.Frame:
    m = fr.rotation_about((0, 0, 0), (0, 0, 1), turn_deg)
    if z_down:
        m = m @ fr.flip_x()
    m[:3, 3] = origin
    return m


def test_addresses_parse_and_print_back() -> None:
    path = InstancePath.parse("tilt/planet[2]")
    assert [str(s) for s in path.segments] == ["tilt", "planet[2]"] and str(path) == "tilt/planet[2]"
    assert str(End.parse("planet[1].bore")) == "planet[1].bore"
    a, b = parse_mate("lid.bottom+housing.top")
    assert (a.owner.name, a.connector, b.owner.name) == ("lid", "bottom", "housing")
    for bad in ("lid", "1lid.x", "a/b.c", "lid.bottom.top"):
        with pytest.raises(ValueError):
            End.parse(bad)


def design(**extra) -> dict:
    base = {
        "name": "d",
        "backend": "build123d",
        "parameters": [{"name": "w", "value": 10, "min": 1, "max": 20}],
        "parts": {"a": {"role": "x", "file": "parts/a.py"}},
        "ground": "a",
    }
    return base | extra


def test_design_json_names_must_be_unique_and_resolve() -> None:
    DesignSpec.model_validate(design())
    with pytest.raises(ValidationError, match="names both"):
        DesignSpec.model_validate(design(components={"w": {"design": "../x"}}))
    with pytest.raises(ValidationError, match="ground"):
        DesignSpec.model_validate(design(ground="nothing"))
    with pytest.raises(ValidationError, match="not a parameter"):
        DesignSpec.model_validate(design(configurations={"big": {"height": 3}}))
    with pytest.raises(ValidationError, match="above maximum"):
        DesignSpec.model_validate(design(configurations={"big": {"w": 30}}))
    with pytest.raises(ValidationError, match="different backends"):
        DesignSpec.model_validate(
            design(
                parts={
                    "a": {"role": "x", "file": "parts/a.py"},
                    "b": {"role": "y", "file": "parts/a.py", "backend": "blender"},
                }
            )
        )
    with pytest.raises(ValidationError, match="bought, not manufactured"):
        DesignSpec.model_validate(
            design(
                parts={
                    "a": {
                        "role": "x",
                        "file": "parts/a.py",
                        "manufacturing": {"process": "fdm"},
                        "catalog": {"designation": "M3", "fidelity": "exact"},
                    }
                }
            )
        )
    with pytest.raises(ValidationError, match="under parts/"):
        DesignSpec.model_validate(design(parts={"a": {"role": "x", "file": "a.py"}}))


def test_configurations_apply_over_the_parameters() -> None:
    d = DesignSpec.model_validate(design(configurations={"big": {"w": 15}}))
    assert d.values() == {"w": 10.0} and d.values("big") == {"w": 15.0}
    with pytest.raises(ValueError, match="no configuration"):
        d.values("small")


def test_a_rigid_mate_checks_origin_axis_and_clocking_up_to_symmetry() -> None:
    mate = RigidMate(type="rigid", a="x.a", b="y.b")
    assert mate.verify(frame(), frame(z_down=True), 1, TOL).passed
    off = mate.verify(frame(), frame((0.2, 0, 0), z_down=True), 1, TOL)
    assert not off.passed and off.values["origin_mm"] == pytest.approx(0.2)
    turned = frame(z_down=True, turn_deg=90)
    assert not mate.verify(frame(), turned, 1, TOL).passed  # a bolt pattern turned a quarter
    assert mate.verify(frame(), turned, 4, TOL).passed  # … that looks the same every quarter turn
    assert mate.verify(frame(), frame(z_down=True, turn_deg=37), "axial", TOL).passed
    assert symmetry_of(4, 2) == 2 and symmetry_of("axial", 4) == 4 and symmetry_of("axial", "axial") == "axial"


def test_a_cylindrical_mate_leaves_turning_and_sliding_free_and_flip_reverses_b() -> None:
    mate = CylindricalMate(type="cylindrical", a="x.a", b="y.b")
    check = mate.verify(frame(), frame((0, 0, 5), z_down=True, turn_deg=30), "axial", TOL)
    assert check.passed and check.free["travel"] == pytest.approx(5)
    assert not mate.verify(frame(), frame((0.5, 0, 5), z_down=True), "axial", TOL).passed
    flipped = CylindricalMate(type="cylindrical", a="x.a", b="y.b", flip=True)
    assert flipped.verify(frame(), flipped.facing(frame((0, 0, 5))), "axial", TOL).passed


def test_gear_relations_check_centre_distance_and_give_ratios() -> None:
    gear = GearRelation(type="gear", a="s.t", b="p.t")
    sun, planet = {"module": 1.25, "teeth": 18, "width": 10}, {"module": 1.25, "teeth": 18, "width": 10}
    assert gear.verify(frame(), frame((22.5, 0, 0)), sun, planet, TOL).passed
    assert not gear.verify(frame(), frame((22.6, 0, 0)), sun, planet, TOL).passed
    ring = {"module": 1.25, "teeth": 54, "width": 10, "internal": 1.0}
    assert gear.verify(frame((22.5, 0, 0)), frame(), planet, ring, TOL).passed
    assert gear.ratio(sun, planet) == pytest.approx(-1.0) and gear.ratio(planet, ring) == pytest.approx(18 / 54)


def test_relations_drive_each_end_the_way_it_physically_moves() -> None:
    for axis, value in ((Axis("angle", np.array([5.0, 0, 0]), np.array([0, 0, 1.0])), 23.0),
                        (Axis("travel", np.array([5.0, 0, 0]), np.array([0, 1.0, 0])), 4.0)):  # fmt: skip
        assert axis.of(axis.motion(value)) == pytest.approx(value)
    # an external mesh turns the two gears opposite ways, whichever way the driven gear's Z points
    gear = GearRelation(type="gear", a="s.t", b="p.t")
    sun_axis, planet_axis = gear.axes(frame(), frame((22.5, 0, 0), z_down=True))
    planet_turn = fr.rotation_about((22.5, 0, 0), (0, 0, 1), -10)
    assert planet_axis.of(planet_turn) == pytest.approx(-10) and np.allclose(planet_axis.direction, (0, 0, 1))
    # a pinion turning anticlockwise seen from above moves a rack below it towards +X
    rack_frame = np.eye(4)
    rack_frame[:3, :3] = np.column_stack(((1, 0, 0), (0, 0, -1), (0, 1, 0)))  # X along the rack, Z up to the pinion
    rack_frame[:3, 3] = (0, -10, 0)
    _, travel = RackRelation(type="rack", a="p.t", b="r.l").axes(frame(), rack_frame)
    assert np.allclose(travel.direction, (1, 0, 0))
    # a right-hand lead screw turning positively about its Z draws its nut back along it
    _, nut = ScrewRelation(type="screw", a="s.axis", b="n.axis", lead=8).axes(frame(), frame((0, 0, 30)))
    assert np.allclose(nut.direction, (0, 0, -1))


def test_fits_come_as_clearance_ranges_or_iso_286() -> None:
    assert Fit(clearance=(0, 0.1)).limits(5) == (0, 0.1)
    assert fit_clearance("H7/g6", 8) == pytest.approx((0.005, 0.029))
    assert Fit(iso="H7/p6").allows_interference(22) and not Fit(iso="H7/g6").allows_interference(22)
    with pytest.raises(ValidationError):
        Fit()
    with pytest.raises(ValueError, match="ISO 286"):
        fit_clearance("loose", 8)


def test_verification_takes_settings_where_the_design_says_nothing() -> None:
    resolved = Verification(angle_deg=0.5).resolve(TOL)
    assert resolved.position_mm == TOL.position_mm and resolved.angle_deg == 0.5


def test_projects_add_and_override_materials(tmp_path: Path) -> None:
    (tmp_path / "materials.json").write_text(
        json.dumps(
            {
                "PLA": {"density_g_cm3": 1.3, "min_thread_engagement_d": 2.5},
                "wood": {"density_g_cm3": 0.6, "min_thread_engagement_d": 3},
            }
        ),
        encoding="utf-8",
    )
    design = tmp_path / "designs" / "a"
    design.mkdir(parents=True)
    registry = materials.for_design(design)
    assert registry["PLA"].density_g_cm3 == 1.3 and "wood" in registry and "steel" in registry


def test_frames_turn_about_any_axis() -> None:
    m = fr.rotation_about((1, 0, 0), (0, 0, 1), 90)
    assert np.allclose(m @ np.array([2, 0, 0, 1.0]), [1, 1, 0, 1])
    assert fr.reduce_angle(91, 4) == pytest.approx(1) and fr.reduce_angle(-179, None) == pytest.approx(-179)


def test_view_quaternions_hold_for_every_rotation() -> None:
    from hitlcad.analysis.cadview import quaternion

    def rotate(q: tuple[float, ...], v: np.ndarray) -> np.ndarray:
        x, y, z, w = q
        u = np.array([x, y, z])
        return 2 * np.dot(u, v) * u + (w * w - np.dot(u, u)) * v + 2 * w * np.cross(u, v)

    half_turn = np.diag([1.0, -1.0, -1.0, 1.0]) @ fr.rotation_about((0, 0, 0), (0, 0, 1), 90)  # about (1, -1, 0)
    for m in (
        half_turn,
        fr.rotation_about((0, 0, 0), (1, 2, 3), 179.9),
        fr.rotation_about((0, 0, 0), (0, 1, 0), 180),
        np.eye(4),
    ):
        q = quaternion(m)
        for v in np.eye(3):
            assert np.allclose(rotate(q, v), m[:3, :3] @ v)


def test_overlapping_shells_load_as_the_solid_they_make(tmp_path: Path) -> None:
    import trimesh

    from hitlcad.analysis.mesh import load_mesh

    # A body with a detail half sunk into it, modelled as two objects: one solid of their union's volume
    body, detail = trimesh.creation.box((20, 20, 20)), trimesh.creation.box((10, 10, 10)).apply_translation((0, 0, 10))
    trimesh.util.concatenate([body, detail]).export(tmp_path / "figure.stl")
    figure = load_mesh(tmp_path / "figure.stl")
    assert len(figure.split()) == 1 and figure.volume == pytest.approx(8000 + 500)
    # A sealed hollow stays one: an inward-facing shell is cut from the solid around it
    hollow = trimesh.creation.box((10, 10, 10))
    hollow.invert()
    trimesh.util.concatenate([body, hollow]).export(tmp_path / "hollow.stl")
    assert load_mesh(tmp_path / "hollow.stl").volume == pytest.approx(8000 - 1000)
    # Shells apart stay apart
    trimesh.util.concatenate([body, detail.apply_translation((50, 0, 0))]).export(tmp_path / "apart.stl")
    assert len(load_mesh(tmp_path / "apart.stl").split()) == 2
