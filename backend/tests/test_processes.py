"""Manufacturing processes on synthetic parts: each check fails on the feature it is about and passes without it."""

from __future__ import annotations

from pathlib import Path

import pytest
from build123d import (
    Align,
    Axis,
    Box,
    BuildPart,
    Locations,
    Mode,
    Pos,
    export_step,
    export_stl,
    fillet,
)

from hitlcad.analysis.model import AnalysisSettings, Body, Leaf, PartGeometry
from hitlcad.core import frames as fr
from hitlcad.core.checks import CncMillingProcess, InjectionMoldingProcess, SheetMetalProcess
from hitlcad.core.report import Reads
from hitlcad.core.structure import PartSpec, Tolerances

pytestmark = pytest.mark.geometry
ON = (Align.CENTER, Align.CENTER, Align.MIN)
SETTINGS = AnalysisSettings(tolerances=Tolerances(position_mm=0.01, angle_deg=0.05, motion_samples=8), overlap_mm3=1e-3,
                            mesh_tolerance_mm=0.01, wall_samples=3000)  # fmt: skip


def body(shape, tmp_path: Path, name: str) -> Body:
    step, stl = tmp_path / f"{name}.step", tmp_path / f"{name}.stl"
    export_step(shape, str(step))
    export_stl(shape, str(stl), tolerance=0.01, angular_tolerance=0.1)
    part = PartGeometry(name=name, spec=PartSpec(role="x", file="parts/x.py"), file="parts/x.py", backend="build123d",
                        mesh_file=stl, step_file=step, objects_file=None, frames={}, catalog=None, reads=Reads(),
                        cached=False)  # fmt: skip
    return Body(name, [Leaf(name, part, None, fr.IDENTITY)], SETTINGS)  # type: ignore[arg-type]


def passed(findings, name: str) -> bool | None:
    return next(f.passed for f in findings if f.name == name)


def pocket(radius: float) -> object:
    with BuildPart() as block:
        Box(60, 40, 20, align=ON)
        with Locations(Pos(0, 0, 8)):
            Box(30, 20, 12, align=ON, mode=Mode.SUBTRACT)
        if radius:
            fillet(block.edges().filter_by(Axis.Z).group_by(Axis.Z)[-1].filter_by_position(Axis.Z, 8, 20), radius)
    return block.part


def test_cnc_milling_wants_inside_corners_a_cutter_can_reach(tmp_path: Path) -> None:
    rules = CncMillingProcess(process="cnc_milling", tool_diameter_mm=6)
    sharp = rules.checks(body(Box(60, 40, 20, align=ON) - Pos(0, 0, 8) * Box(30, 20, 12, align=ON), tmp_path, "sharp"))
    assert passed(sharp, "cnc_inside_corners") is False
    under = Box(40, 40, 20, align=ON) - Pos(0, 0, 5) * Box(50, 10, 5, align=ON)  # a slot through the side: an undercut
    assert passed(rules.checks(body(under, tmp_path, "under")), "cnc_setups") is False
    assert passed(rules.checks(body(Box(40, 40, 20, align=ON), tmp_path, "block")), "cnc_inside_corners") is True


def test_sheet_metal_wants_one_thickness_and_bends_of_a_radius(tmp_path: Path) -> None:
    rules = SheetMetalProcess(process="sheet_metal", thickness_mm=2, min_bend_radius_mm=1)
    flat = rules.checks(body(Box(80, 40, 2, align=ON), tmp_path, "flat"))
    assert passed(flat, "sheet_thickness") is True and passed(flat, "sheet_bend_radius") is True
    angle = Box(80, 40, 2, align=ON) + Pos(39, 0, 0) * Box(2, 40, 30, align=ON)  # a sharp inside bend
    assert passed(rules.checks(body(angle, tmp_path, "angle")), "sheet_bend_radius") is False
    assert passed(rules.checks(body(Box(80, 40, 6, align=ON), tmp_path, "plate")), "sheet_thickness") is False


def test_molding_wants_draft_and_even_walls(tmp_path: Path) -> None:
    rules = InjectionMoldingProcess(process="injection_molding", min_draft_deg=1)
    box = rules.checks(body(Box(40, 40, 20, align=ON), tmp_path, "box"))
    assert passed(box, "molding_draft") is False  # straight walls drag on the mold
    lumpy = Box(60, 40, 2, align=ON) + Box(10, 10, 20, align=ON)
    assert passed(rules.checks(body(lumpy, tmp_path, "lumpy")), "molding_walls") is False
