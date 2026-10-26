"""The domain model on its own: addresses, design.json validation, mate and relation geometry, fits, settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from hitlcad import materials
from hitlcad.builtin.gears.relations import GearRelation, RackRelation, ScrewRelation
from hitlcad.builtin.joints.mates import CylindricalMate, RigidMate
from hitlcad.core import frames as fr
from hitlcad.core.address import End, InstancePath
from hitlcad.core.design import DesignSpec
from hitlcad.core.iso286 import deviations, fit_clearance
from hitlcad.core.structure import Axis, Fit, Tolerances, Verification, symmetry_of
from hitlcad.kernel.errors import DesignInvalid, Location

TOL = Tolerances(position_mm=0.01, angle_deg=0.05, motion_steps=2000)


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


def test_an_int_parameter_takes_whole_numbers_only() -> None:
    from hitlcad.core.types import ParamSpec

    assert ParamSpec(name="n", type="int", value=3.0).value == 3
    with pytest.raises(ValidationError, match="n: expected a whole number, got 2.7"):
        ParamSpec(name="n", type="int", value=2.7)


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


ISO_286_2 = [  # (class, nominal size, upper, lower in µm), from ISO 286-2's tables
    ("H7", 2, 10, 0), ("h6", 2, 0, -6), ("H7", 3, 10, 0), ("h6", 3, 0, -6), ("H7", 3.001, 12, 0),
    ("g6", 8, -5, -14), ("s6", 8, 32, 23), ("d9", 8, -40, -76), ("c11", 8, -80, -170), ("e8", 8, -25, -47),
    ("u6", 30, 61, 48), ("js7", 8, 7.5, -7.5), ("J7", 4, 6, -6), ("K7", 10, 5, -10), ("K4", 5, 0.5, -3.5),
    ("M6", 300, -9, -41), ("N9", 100, 0, -87), ("N9", 600, -44, -219), ("P7", 50, -17, -42), ("P7", 600, -78, -148),
    ("S7", 10, -17, -32), ("t6", 2900, 2235, 2100), ("s6", 3150, 1535, 1400), ("H11", 3000, 1350, 0),
]  # fmt: skip


@pytest.mark.parametrize(("cls", "size", "upper", "lower"), ISO_286_2, ids=[f"{c}@{s}" for c, s, *_ in ISO_286_2])
def test_iso_286_limits_follow_the_standard_over_its_whole_range(
    cls: str, size: float, upper: float, lower: float
) -> None:
    assert deviations(cls, size) == pytest.approx((upper / 1000, lower / 1000))


def test_fits_come_as_clearance_ranges_or_iso_286_and_an_undefined_one_is_named_never_raised() -> None:
    assert Fit(clearance=(0, 0.1)).limits(5) == (0, 0.1)
    assert fit_clearance("H7/g6", 8) == pytest.approx((0.005, 0.029))
    assert Fit(iso="H7/p6").allows_interference(22) and not Fit(iso="H7/g6").allows_interference(22)
    with pytest.raises(ValidationError):
        Fit()
    with pytest.raises(ValueError, match="ISO 286"):
        fit_clearance("loose", 8)
    for fit, size, why in (("H7/t6", 10, "only above 24 up to 3150 mm"), ("H11/c11", 600, "only above 0 up to 500"),
                           ("H7/g6", 3200, "outside ISO 286's sizes"), ("K9/h9", 10, "only up to 3 mm")):  # fmt: skip
        with pytest.raises(ValueError, match=why):
            Fit(iso=fit).limits(size)
        assert not Fit(iso=fit).allows_interference(size)  # the interference and motion checks never crash on it
    with pytest.raises(ValidationError, match="holes A, B, C"):
        Fit(iso="Q7/g6")
    assert fit_clearance("H7/s6", 8) == pytest.approx((-0.032, -0.008))  # classes isofits lacked
    assert fit_clearance("H9/d9", 8) == pytest.approx((0.04, 0.112)) and fit_clearance("H11/c11", 8)[0] == 0.08


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


def test_view_quaternions_hold_for_every_rotation() -> None:
    from hitlcad.geometry.cadview import quaternion

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

    from hitlcad.geometry.mesh import load_mesh

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


def _part(tmp_path: Path, name: str, mesh: Any) -> Any:
    from hitlcad.analysis.model import PartGeometry
    from hitlcad.core.design import PartSpec
    from hitlcad.core.report import Reads

    mesh.export(tmp_path / f"{name}.stl")
    return PartGeometry(name=name, spec=PartSpec(role="x", file="parts/x.py"), file="parts/x.py", backend="build123d",
                        mesh_file=tmp_path / f"{name}.stl", step_file=None, objects_file=tmp_path / f"{name}.glb",
                        frames={}, catalog=None, reads=Reads(), cached=False)  # fmt: skip


def test_one_closedness_test_measures_solids_that_touch_along_an_edge(tmp_path: Path) -> None:
    import trimesh

    # Two blocks meeting along one edge, as a bearing's balls meet its races: trimesh reads the edge four
    # triangles share as open; manifold3d, which measures every overlap and gap, reads a closed solid
    blocks = trimesh.util.concatenate([trimesh.creation.box((10, 10, 10)),
                                       trimesh.creation.box((10, 10, 10)).apply_translation((10, 10, 0))])  # fmt: skip
    part = _part(tmp_path, "blocks", blocks)
    assert not part.mesh.is_watertight
    assert part.metrics.watertight and part.metrics.volume == pytest.approx(2000) and part.manifold.volume() > 0


def test_a_body_of_many_leaves_is_measured_from_its_parts_not_one_mesh_of_them(tmp_path: Path) -> None:
    import trimesh

    from hitlcad.analysis.model import AnalysisSettings, Body, Leaf

    settings = AnalysisSettings(overlap_mm3=1e-3, mesh_tolerance_mm=0.01, wall_samples=100)
    part = _part(tmp_path, "cube", trimesh.creation.box((10, 10, 10)))
    leaves = [Leaf(f"cube[{k}]", part, None, fr.translation((20.0 * k, 0, 0))) for k in range(3)]  # type: ignore[arg-type]
    body = Body("row", leaves, settings, TOL)
    assert body.metrics.size == pytest.approx((50, 10, 10)) and body.metrics.volume == pytest.approx(3000)
    assert body.metrics.watertight and body.metrics.bodies == 3
    assert "mesh" not in body.__dict__  # a box and a volume need no mesh of every leaf at once


def test_design_json_problems_are_named_where_they_are(tmp_path: Path) -> None:
    from hitlcad.workspace.folder import DesignFolder

    (tmp_path / "design.json").write_text(json.dumps({"hitlcad": 1, "name": "old", "source": "model.py"}),
                                          encoding="utf-8")  # fmt: skip
    with pytest.raises(DesignInvalid) as old:
        DesignFolder(tmp_path).load()
    (problem,) = old.value.problems  # one problem, not one per field the old format no longer matches
    assert "hitlcad format 1" in problem.message and problem.code == "design.invalid"
    design = {"name": "x", "backend": "build123d", "parts": {"a": {"role": "a", "file": "parts/a.py", "colour": "red"}}}
    (tmp_path / "design.json").write_text(json.dumps(design), encoding="utf-8")
    with pytest.raises(DesignInvalid, match=r"\nparts\.a\.colour: ") as unknown:
        DesignFolder(tmp_path).load()
    (problem,) = unknown.value.problems
    assert problem.where == Location(file="design.json", address="parts.a.colour")


def test_a_gear_pattern_whose_nominals_do_not_resolve_fails_every_frame() -> None:
    """Its relations then say so for every frame, never reading the nominals that are not there."""
    from hitlcad.analysis.base import Context
    from hitlcad.analysis.model import BuiltDesign, PartGeometry
    from hitlcad.analysis.relations import Relations
    from hitlcad.core.report import Reads

    gears = {"teeth": {"kind": "gear", "count": 2, "teeth": "teeth", "module": "interfaces.module", "width": "w"}}
    spec = DesignSpec.model_validate(design(
        parts={name: {"role": name, "file": "parts/a.py", "connectors": gears} for name in ("a", "b")},
        relations=[{"type": "gear", "a": "a.teeth", "b": "b.teeth"}],
    ))  # fmt: skip
    parts = {name: PartGeometry(name=name, spec=spec.parts[name], file="parts/a.py", backend="build123d",
                                mesh_file=Path("unused.stl"), step_file=None, objects_file=Path("unused.glb"),
                                frames={"teeth": [fr.IDENTITY, fr.translation((0, 30, 0))]}, catalog=None,
                                reads=Reads(), cached=False)
             for name in ("a", "b")}  # fmt: skip
    built = BuiltDesign(spec=spec, folder=Path(), values={"w": 10}, interfaces={}, interface_reads={},
                        assembly_reads=Reads(), parts=parts, placements={"a": [fr.IDENTITY], "b": [fr.IDENTITY]},
                        components={}, materials={}, tolerances=TOL)  # fmt: skip
    built.measure_connectors()
    assert [m.detail for m in built.measured[("a", "teeth")]] == ["no parameter 'teeth'"] * 2
    relations = Relations().run(Context(built, None, None, None))  # type: ignore[arg-type]
    assert [r.status for r in relations] == ["unverified"] * 2


def test_results_with_one_cause_are_one_problem_and_pattern_copies_fold() -> None:
    """The README's seeded mistake fails two checks and leaves a motion unverified: one problem. Four screws too
    long are one problem at four indices. Info results are no checks, and the tally counts problems."""
    from hitlcad.agent.graph import problem_lines
    from hitlcad.core.report import BuildReport, Cause, Earlier, Result
    from hitlcad.kernel.errors import advice

    overlap = Cause.overlap("impeller", "duct")
    found = [
        Result(analysis="part", code="part.mesh_open", subject="duct", status="pass", detail="closed mesh"),
        *(Result(analysis="fastener", code="fastener.stack", subject="screw.seat:lid.screws>base.holes", status="fail",
                 detail=f"screw[{k}].seat into base.holes[{k}]: bottoms out at {6 + k / 10:.2f}") for k in range(4)),
        Result(analysis="interference", code="interference.overlap", subject="duct+impeller", status="fail",
               detail="overlap by 91.11 mm³", cause=overlap),
        Result(analysis="interference", code="interference.expected", subject="base+insert[0]", status="pass",
               severity="info", detail="expected"),
        Result(analysis="motion", code="motion.partial", subject="motor.shaft+impeller.bore", status="unverified",
               detail="already overlap", cause=overlap),
        Result(analysis="requirement", code="requirement.clearance", subject="Impeller blade tips clear the duct",
               status="fail", detail="impeller and duct overlap by 91.1 mm³", cause=overlap),
        Result(analysis="requirement", code="requirement.unchecked", subject="Finger-safe", status="unverified",
               detail="the human judges it"),
    ]  # fmt: skip
    report = BuildReport(status="succeeded", duration_s=1, results=found)
    assert problem_lines(report.results, report.problems, 10) == [
        "  ✗ fastener screw.seat:lid.screws>base.holes ×4 at [0, 1, 2, 3] [fastener.stack]: screw[0].seat into "
        "base.holes[0]: bottoms out at 6.00",
        "  ✗ interference duct+impeller [interference.overlap]: overlap by 91.11 mm³ (also: motion "
        "motor.shaft+impeller.bore, requirement 'Impeller blade tips clear the duct')",
        "  ? requirement Finger-safe [requirement.unchecked]: the human judges it",
    ]
    # For the agent, the usual fix follows a code's first problem; one the revision before had too says so.
    before = Earlier(4, frozenset({("fastener.stack", "screw.seat:lid.screws>base.holes")}))
    lines = problem_lines(report.results, report.problems, 10, hints=True, previous=before)
    assert lines[1] == f"    → {advice('fastener.stack')}" and advice("fastener.stack").startswith("Size the clearance")
    assert lines[2] == "    Same as revision 4: the change since did not reach it."
    assert lines[4].startswith("    → Move or resize one of them")
    assert report.tally.text == "2 problems (6 failing checks), 1 unverified; 1 check passes"
    assert BuildReport.model_validate_json(report.model_dump_json()).problems == report.problems
