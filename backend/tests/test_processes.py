"""Manufacturing processes on synthetic parts: each check fails on the feature it is about and passes without it."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from build123d import (
    Align,
    Axis,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    Cone,
    Cylinder,
    Locations,
    Mode,
    Plane,
    Polygon,
    Polyline,
    Pos,
    Rectangle,
    Rotation,
    Sphere,
    export_gltf,
    export_step,
    export_stl,
    extrude,
    fillet,
    make_face,
)

from hitlcad.analysis.model import AnalysisSettings, Body, Leaf, PartGeometry
from hitlcad.core import frames as fr
from hitlcad.core.checks import (
    CncMillingProcess,
    FdmProcess,
    InjectionMoldingProcess,
    ResinProcess,
    SheetMetalProcess,
)
from hitlcad.core.report import Reads
from hitlcad.core.structure import PartSpec, Tolerances

pytestmark = pytest.mark.geometry
ON = (Align.CENTER, Align.CENTER, Align.MIN)
SETTINGS = AnalysisSettings(tolerances=Tolerances(position_mm=0.01, angle_deg=0.05, motion_steps=2000), overlap_mm3=1e-3,
                            mesh_tolerance_mm=0.01, wall_samples=3000)  # fmt: skip


def body(shape, tmp_path: Path, name: str) -> Body:
    step, stl, glb = tmp_path / f"{name}.step", tmp_path / f"{name}.stl", tmp_path / f"{name}.glb"
    export_step(shape, str(step))
    export_stl(shape, str(stl), tolerance=0.01, angular_tolerance=0.1)
    export_gltf(shape, str(glb), binary=True, linear_deflection=0.01, angular_deflection=0.1)
    part = PartGeometry(name=name, spec=PartSpec(role="x", file="parts/x.py"), file="parts/x.py", backend="build123d",
                        mesh_file=stl, step_file=step, objects_file=glb, frames={}, catalog=None, reads=Reads(),
                        cached=False)  # fmt: skip
    return Body(name, [Leaf(name, part, None, fr.IDENTITY)], SETTINGS)  # type: ignore[arg-type]


def finding(findings, name: str):
    return next(f for f in findings if f.name == name)


def passed(findings, name: str) -> bool | None:
    return finding(findings, name).passed


def pocket(radius: float) -> object:
    with BuildPart() as block:
        Box(60, 40, 20, align=ON)
        with Locations(Pos(0, 0, 8)):
            Box(30, 20, 12, align=ON, mode=Mode.SUBTRACT)
        if radius:
            fillet(block.edges().filter_by(Axis.Z).group_by(Axis.Z)[-1].filter_by_position(Axis.Z, 8, 20), radius)
    return block.part


def bracket() -> object:
    """An L bent from 2 mm sheet: inside radius 2, outside 4."""
    with BuildPart() as bent:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline((0, 0), (40, 0), (40, 30), (38, 30), (38, 2), (0, 2), close=True)
            make_face()
        extrude(amount=20)
        x_edges = bent.edges().filter_by(Axis.Y)
        fillet(x_edges.filter_by_position(Axis.X, 37.9, 38.1).filter_by_position(Axis.Z, 1.9, 2.1), 2)
        fillet(
            bent.edges().filter_by(Axis.Y).filter_by_position(Axis.X, 39.9, 40.1).filter_by_position(Axis.Z, -0.1, 0.1),
            4,
        )
    return bent.part


def tray(taper: float) -> object:
    """A 2 mm shell open at the top, its walls turned `taper`° from Z, outside and in alike."""
    grow = 2 * 20 * math.tan(math.radians(taper))
    with BuildPart() as shell:
        with BuildSketch():
            Rectangle(60, 40)
        extrude(amount=20, taper=-taper)
        with BuildSketch(Plane.XY.offset(20)):
            Rectangle(60 + grow - 4, 40 + grow - 4)
        extrude(amount=-18, taper=taper, mode=Mode.SUBTRACT)
    return shell.part


def test_wall_thickness_is_the_wall_not_the_ray(tmp_path: Path) -> None:
    """A ray along a shell (from its rim) or past a corner reads the wall it runs in; a thin spot covering an
    area is found and measured even between the even samples."""
    shell = body(tray(2), tmp_path, "tray").walls
    measured = shell.thin[: shell.even][shell.measured]
    assert abs(shell.thinnest - 2) < 0.05 and measured.max() < 3  # the rims' and ends' rays ran 20-60 mm
    block = body(Box(40, 40, 20, align=ON), tmp_path, "block").walls
    assert block.thinnest > 19.9  # no thin reading at its edges and corners
    finned = body(Box(30, 30, 10, align=ON) + Pos(0, 0, 10) * Box(20, 0.5, 5, align=ON), tmp_path, "fin").walls
    assert abs(finned.thinnest - 0.5) < 0.02 and abs(finned.at[1]) <= 0.26
    with BuildPart() as blade:  # a 30° knife edge: it tapers to nothing, and prints rounded off; no wall
        with BuildSketch(Plane.XZ):
            Polygon((0, 0), (20, 0), (0, 20 * math.tan(math.radians(30))), align=None)
        extrude(amount=20)
    assert body(blade.part, tmp_path, "blade").walls.thinnest > 2


def test_cnc_milling_wants_inside_corners_a_cutter_can_reach(tmp_path: Path) -> None:
    rules = CncMillingProcess(process="cnc_milling", tool_diameter_mm=6)
    sharp = rules.checks(body(pocket(0), tmp_path, "sharp"))
    assert passed(sharp, "cnc_inside_corners") is False
    assert (
        "4 sharp vertical inside corners: 12.0 mm long at (-15.0, -10.0, 14.0)"
        in finding(sharp, "cnc_inside_corners").detail
    )
    for radius in (3, 4):  # the tool's radius and more: the fillets' tangent edges are no corners
        rounded = rules.checks(body(pocket(radius), tmp_path, f"r{radius}"))
        assert passed(rounded, "cnc_inside_corners") is True, finding(rounded, "cnc_inside_corners").detail
    deep = rules.checks(body(Box(30, 30, 50, align=ON) - Pos(0, 0, 10) * Box(8, 8, 40, align=ON), tmp_path, "deep"))
    assert (
        passed(deep, "cnc_pocket_depth") is False
        and "40.0 mm deep for a 6 mm cutter" in finding(deep, "cnc_pocket_depth").detail
    )
    under = Box(40, 40, 20, align=ON) - Pos(0, 0, 5) * Box(50, 10, 5, align=ON)  # a slot through the side: an undercut
    setups = finding(rules.checks(body(under, tmp_path, "under")), "cnc_setups")
    assert setups.passed is False and "1 region: " in setups.detail
    assert passed(rules.checks(body(Box(40, 40, 20, align=ON), tmp_path, "block")), "cnc_inside_corners") is True


def test_sheet_metal_wants_one_thickness_and_bends_of_a_radius(tmp_path: Path) -> None:
    rules = SheetMetalProcess(process="sheet_metal", thickness_mm=2, min_bend_radius_mm=2)
    flat = rules.checks(body(Box(80, 40, 2, align=ON), tmp_path, "flat"))
    assert passed(flat, "sheet_thickness") is True and passed(flat, "sheet_bend_radius") is True
    bent = rules.checks(body(bracket(), tmp_path, "bent"))
    assert passed(bent, "sheet_thickness") is True and finding(bent, "sheet_bend_radius").detail.startswith("1 bend,")
    cut = rules.checks(body(Box(80, 40, 2) - Box(10, 10, 4), tmp_path, "cut"))  # a cut-out's corners are no bends
    assert passed(cut, "sheet_bend_radius") is True
    angle = Box(80, 40, 2, align=ON) + Pos(39, 0, 0) * Box(2, 40, 30, align=ON)  # a sharp inside bend
    sharp = finding(rules.checks(body(angle, tmp_path, "angle")), "sheet_bend_radius")
    assert sharp.passed is False and "1 sharp inside bend: 40.0 mm long at (38.0, 0.0, 2.0)" in sharp.detail
    assert passed(rules.checks(body(Box(80, 40, 6, align=ON), tmp_path, "plate")), "sheet_thickness") is False


def test_molding_wants_draft_no_undercuts_and_even_walls(tmp_path: Path) -> None:
    rules = InjectionMoldingProcess(process="injection_molding", min_draft_deg=1)
    box = rules.checks(body(Box(40, 40, 20, align=ON), tmp_path, "box"))
    assert passed(box, "molding_draft") is False  # straight walls drag on the mold
    ball = rules.checks(body(Sphere(10), tmp_path, "ball"))  # its equator only turns through vertical
    assert passed(ball, "molding_draft") is True and passed(ball, "molding_undercuts") is True
    with BuildPart() as waisted:
        Cone(20, 15, 10, align=ON)
        with Locations(Pos(0, 0, 10)):
            Cone(15, 20, 10, align=ON)
    hourglass = rules.checks(body(waisted.part, tmp_path, "hourglass"))
    assert passed(hourglass, "molding_draft") is True and passed(hourglass, "molding_undercuts") is False
    drafted = rules.checks(body(tray(2), tmp_path, "tray"))
    assert all(f.passed for f in drafted), drafted  # a 2 mm shell: its rims and ends are no thick sections
    assert passed(rules.checks(body(tray(-2), tmp_path, "narrowing")), "molding_undercuts") is False
    lumpy = Box(60, 40, 2, align=ON) + Box(10, 10, 20, align=ON)
    assert (
        "over 2 × its median wall (2.00 mm)"
        in finding(rules.checks(body(lumpy, tmp_path, "lumpy")), "molding_walls").detail
    )


def test_fdm_overhangs_print_where_bridged_and_fail_unless_on_support(tmp_path: Path) -> None:
    fdm = FdmProcess(process="fdm")
    tee = Box(10, 10, 20, align=ON) + Pos(0, 0, 20) * Box(40, 10, 5, align=ON)  # arms out flat: 90° overhangs
    shape = body(tee, tmp_path, "tee")
    arms = finding(fdm.checks(shape), "fdm_overhangs")
    assert (
        arms.passed is False and "2 regions: " in arms.detail and "reaching 15.0 mm past what holds it" in arms.detail
    )
    supported = FdmProcess(process="fdm", supports=True).checks(shape)
    assert passed(supported, "fdm_overhangs") is True
    assert "prints on support" in finding(supported, "fdm_overhangs").detail
    m3 = Box(20, 5, 10, align=ON) - Pos(0, 0, 5) * Rotation(90, 0, 0) * Cylinder(1.7, 20)  # an M3 clearance hole
    assert passed(fdm.checks(body(m3, tmp_path, "m3")), "fdm_overhangs") is True
    short = fdm.checks(body(Box(28, 10, 20, align=ON) - Box(8, 20, 15, align=ON), tmp_path, "short"))
    assert passed(short, "fdm_overhangs") is True  # an 8 mm bridge
    shape = body(Box(40, 10, 20, align=ON) - Box(20, 20, 15, align=ON), tmp_path, "long")
    long = finding(fdm.checks(shape), "fdm_overhangs")
    assert long.passed is False and "bridging 20.0 mm" in long.detail
    assert passed(FdmProcess(process="fdm", max_bridge_mm=25).checks(shape), "fdm_overhangs") is True


def test_fdm_bed_contact_is_the_base_under_its_outline_and_its_height(tmp_path: Path) -> None:
    fdm = FdmProcess(process="fdm")
    for name, shape in [("bar", Box(100, 5, 5, align=ON)), ("turned", Rotation(0, 0, 45) * Box(100, 5, 5, align=ON)),
                        ("ring", Cylinder(50, 5, align=ON) - Cylinder(48, 5, align=ON))]:  # fmt: skip
        assert passed(fdm.checks(body(shape, tmp_path, name)), "fdm_bed_contact") is True, name
    tower = body(Box(5, 5, 100, align=ON), tmp_path, "tower")
    tall = finding(fdm.checks(tower), "fdm_bed_contact")
    assert tall.passed is False and "20.0 × that tall" in tall.detail and "brim" in tall.detail
    assert passed(FdmProcess(process="fdm", brim=True).checks(tower), "fdm_bed_contact") is True
    ball = fdm.checks(body(Pos(0, 0, 10) * Sphere(10), tmp_path, "ball"))
    assert passed(ball, "fdm_bed_contact") is False and passed(ball, "fdm_overhangs") is False


def test_resin_wants_hollows_drained_and_walls_that_survive(tmp_path: Path) -> None:
    rules = ResinProcess(process="resin", min_wall_mm=0.6)
    hollow = Box(30, 30, 30, align=ON) - Pos(0, 0, 3) * Box(24, 24, 24, align=ON)
    sealed = finding(rules.checks(body(hollow, tmp_path, "hollow")), "resin_drains")
    assert sealed.passed is False and "sealed at (0.0, 0.0, 15.0)" in sealed.detail
    vented = rules.checks(body(hollow - Cylinder(0.25, 6, align=ON), tmp_path, "vented"))  # 0.5 mm: too narrow
    assert passed(vented, "resin_drains") is False
    drained = hollow - Cylinder(2, 6, align=ON)  # a 4 mm drain hole through the floor into the hollow
    assert passed(rules.checks(body(drained, tmp_path, "drained")), "resin_drains") is True
    assert passed(rules.checks(body(Box(30, 30, 0.4, align=ON), tmp_path, "film")), "resin_wall_thickness") is False
