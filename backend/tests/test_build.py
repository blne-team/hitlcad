"""Real builds through the pipeline: the reference designs verify as designed, and seeded faults are caught by
the analysis that owns them."""

from __future__ import annotations

import csv
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from hitlcad.app import HitlCad
from hitlcad.core.report import BuildReport
from hitlcad.designs.folder import DesignFolder

from .conftest import copy_design, results, write_design

pytestmark = pytest.mark.geometry


async def build(core: HitlCad, folder: Path, out: Path, configuration: str | None = None) -> BuildReport:
    spec = DesignFolder(folder).spec(core.settings.tolerances, configuration)
    return await core.pipeline.run(spec, folder, out)


def failing(report: BuildReport) -> list[str]:
    return [f"{r.analysis} {r.subject}: {r.detail}" for r in report.results if r.status == "fail"]


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new))


def edit_json(path: Path, change: Any) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2))


async def test_the_enclosure_verifies_and_exports(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    assert failing(report) == []
    fasteners = results(report, "fastener", "pass")
    assert len(fasteners) == 4 and all("engages 5.00 mm" in r.detail for r in fasteners)
    assert len(report.instances) == 10 and report.mass and report.mass.mass_g > 20
    out = tmp_path / "out"
    for rel in ("model.step", "mesh.stl", "view.json", "parts/base.step", "parts/lid.3mf", "model.3mf", "bom.csv"):
        assert (out / rel).is_file(), rel
    rows = list(csv.DictReader((out / "bom.csv").open()))
    assert {r["name"]: int(r["quantity"]) for r in rows} == {"base": 1, "lid": 1, "insert": 4, "screw": 4}
    urdf = ET.parse(next((out / "urdf").glob("*.urdf"))).getroot()
    assert len(urdf.findall("link")) == 10 and all(j.get("type") == "fixed" for j in urdf.findall("joint"))
    assert ET.parse(out / "sdf" / "model.sdf").getroot().find("model") is not None
    view = json.loads((out / "view.json").read_text())
    leaves = [p["id"] for p in view["shapes"]["parts"]]
    assert "/Enclosure/insert[3]" in leaves and len(view["instances"]) == 4  # tessellated once per part


async def test_seeded_faults_are_caught_by_their_analysis(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit(folder / "parts/hardware.py", '"M3-0.5", 9,', '"M3-0.5", 10,')  # too long: bottoms out in the insert
    edit_json(folder / "design.json", lambda d: d["mates"][1].pop("overlap"))  # the inserts' knurls, undeclared
    edit(folder / "assembly.py", '"lid": [Pos(0, 0, h)]', '"lid": [Pos(0, 0, h + 0.2)]')  # the lid floats 0.2 mm up
    report = await build(core, folder, tmp_path / "out")
    text = "\n".join(failing(report))
    assert "mate base.top+lid.bottom" in text and "origin 0.200 mm" in text
    assert (
        "bottoms out: 5.80 mm into a 5.70 mm deep insert[0].thread" not in text
    )  # the lid moved: the stack is shorter
    assert "not seated on lid.screws[0]" in text
    assert "interference base+insert[0]: base and insert[0] overlap" in text


async def test_a_screw_too_long_bottoms_out(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit(folder / "parts/hardware.py", '"M3-0.5", 9,', '"M3-0.5", 10,')
    report = await build(core, folder, tmp_path / "out")
    assert any("bottoms out: 6.00 mm into a 5.70 mm deep" in f for f in failing(report))


async def test_floating_parts_and_broken_references(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit_json(
        folder / "design.json",
        lambda d: (
            d["mates"].pop(1),
            d.update(fasteners=[]),
            d["mates"].append({"type": "rigid", "a": "base.top", "b": "lid.nothing"}),
        ),
    )
    report = await build(core, folder, tmp_path / "out")
    text = "\n".join(failing(report))
    assert "coverage insert[0]: insert[0] is floating" in text and "coverage screw[3]" in text
    assert "part lid declares no connector 'nothing'" in text


async def test_errors_in_a_part_file_name_its_line_and_spare_the_others(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit(
        folder / "parts/hardware.py", "def build(p, i):\n", "def build(p, i):\n    raise ValueError('no such insert')\n"
    )
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "failed"
    assert "parts/hardware.py line 5, in build" in report.error and "ValueError: no such insert" in report.error
    assert [r.subject for r in report.results] == ["parts/hardware.py"]


async def test_parts_come_from_the_cache_when_what_they_read_is_unchanged(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    first = await build(core, folder, tmp_path / "a")
    assert not any(p.cached for p in first.parts)
    edit_json(folder / "design.json", lambda d: d["parameters"][3].update(value=5))  # lid_t, read by parts/box.py
    second = await build(core, folder, tmp_path / "b")
    # the cache works per file: base and lid share parts/box.py, one parametric history
    assert {p.name: p.cached for p in second.parts} == {"base": False, "lid": False, "insert": True, "screw": True}
    assert next(p for p in second.parts if p.name == "lid").reads.parameters == ["height", "lid_t", "wall", "width"]


async def test_configurations_build_their_own_values(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    report = await build(core, folder, tmp_path / "out", "wide")
    assert report.configuration == "wide" and report.values["width"] == 60
    assert report.metrics and report.metrics.size[0] == pytest.approx(60)
    assert failing(report) == [
        "requirement fits 50 mm: 60.00 × 60.00 × 27.00 mm in a 50 × 50 × 30 mm box: X 60.00 > 50, Y 60.00 > 50"
    ]  # the wide variant breaks a requirement the default meets


async def test_structure_is_checked_before_any_geometry(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit_json(
        folder / "design.json", lambda d: d["mates"].append({"type": "rigid", "a": "base.side", "b": "lid.bottom"})
    )
    spec = DesignFolder(folder).spec(core.settings.tolerances)
    found, plan = await core.pipeline.check_structure(spec, folder, tmp_path / "work")
    assert plan and set(plan.placements) == {"base", "lid", "insert", "screw"}
    assert [r.detail for r in found if r.status == "fail"] == [
        "part base declares no connector 'side'; connectors: top, inserts"
    ]


async def test_the_hinge_sweeps_its_range_and_finds_the_stop(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("hinge", tmp_path / "hinge")
    report = await build(core, folder, tmp_path / "a")
    (motion,) = results(report, "motion")
    assert motion.status == "fail" and "lid hits base" in motion.detail
    (sweep,) = report.motions  # what the panel moves: the lid, about the hinge axis, -100 … 0°
    assert (sweep.kind, sweep.start, sweep.end, sweep.moving) == ("angle", -100, 0, ["lid"])
    assert sweep.axis == pytest.approx((1, 0, 0)) and sweep.current == pytest.approx(0, abs=1e-6)
    edit_json(folder / "design.json", lambda d: d["parameters"][6].update(value=0))  # no stop post
    clear = await build(core, folder, tmp_path / "b")
    (motion,) = results(clear, "motion")
    assert motion.status == "pass" and "-100 … 0°" in motion.detail


async def test_the_gearbox_finds_its_one_real_fit_problem(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("gearbox", tmp_path / "gearbox")
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    joints = [
        f for f in failing(report) if f.split()[0] in ("mate", "relation", "fastener", "interference", "coverage")
    ]
    assert joints == [
        "mate motor.shaft+sun.bore: motor.shaft ↔ sun.bore: cylindrical, frames agree; fit 0.200 mm "
        "(hole Ø5.200, shaft Ø5.000), needs 0 … 0.1"
    ]
    assert len(results(report, "relation", "pass")) == 6
    motion = results(report, "motion")  # a planetary is a closed loop: not analysed, never wrongly failed
    assert motion and not [r for r in motion if r.status == "fail"]
    assert any(r.status == "unverified" and "closed loop" in r.detail for r in motion)
    (stack,) = [r for r in results(report, "requirement") if "axial gap" in r.subject]
    assert stack.status == "pass" and stack.detail.startswith(
        "0.500 mm nominal, 0.300 … 0.700 worst case (± 0.200), ± 0.141 RSS"
    )
    ratio_5 = await build(core, folder, tmp_path / "ratio_5", "ratio_5")
    assert ratio_5.status == "succeeded" and len(results(ratio_5, "relation", "pass")) == 6


async def test_components_are_built_bound_and_counted(core: HitlCad, tmp_path: Path) -> None:
    copy_design("hinge", tmp_path / "hinge")
    folder = copy_design("shelf", tmp_path / "shelf")
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    child = report.components["box"]
    assert child.values["size"] == 30  # bound from the shelf's box_size
    assert [r.status for r in results(report, "mate")] == ["pass", "pass"]
    bom = {line.path: (line.quantity, line.level) for line in report.bom}
    assert bom == {"plate": (1, 0), "box": (2, 0), "box/base": (2, 1), "box/lid": (2, 1)}
    motion = results(report, "motion")  # flexible: each box's hinge swings at the shelf's level too
    assert len(motion) == 2 and all(r.status == "fail" and "hits" in r.detail for r in motion)
    assert (tmp_path / "out" / "components" / "box" / "parts" / "lid.step").is_file()


async def test_mesh_parts_join_b_rep_parts(core: HitlCad, tmp_path: Path) -> None:
    folder = write_design(
        tmp_path / "knob",
        {
            "design.json": {
                "hitlcad": 2,
                "name": "Knob",
                "backend": "build123d",
                "ground": "plate",
                "parameters": [{"name": "r", "value": 8, "min": 2, "max": 20}],
                "parts": {
                    "plate": {
                        "role": "base",
                        "file": "parts/plate.py",
                        "material": "PLA",
                        "connectors": {"socket": {"kind": "hole"}},
                    },
                    "knob": {
                        "role": "sculpted grip",
                        "file": "parts/knob.py",
                        "backend": "blender",
                        "material": "PLA",
                        "connectors": {"stem": {"kind": "shaft"}},
                    },
                },
                "mates": [
                    {"type": "cylindrical", "a": "plate.socket", "b": "knob.stem", "fit": {"clearance": [0, 0.3]}}
                ],
            },
            "parts/plate.py": "from build123d import Box, Cylinder, Location, Align\n\n\ndef build(p, i):\n"
            "    plate = Box(40, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN)) - "
            "Cylinder(2, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
            "    return {'plate': (plate, {'socket': Location((0, 0, 6))})}\n",
            "parts/knob.py": "import bpy\nfrom mathutils import Matrix\n\n\ndef build(p, i):\n"
            "    bpy.ops.mesh.primitive_uv_sphere_add(radius=p['r'], location=(0, 0, p['r']))\n"
            "    knob = bpy.context.active_object\n    knob.name = 'grip'\n"
            "    return {'knob': (knob, {'stem': Matrix.Rotation(3.14159265, 4, 'X')})}\n",
            "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'plate': [Pos()], 'knob': [Pos(0, 0, 6)]}\n",
        },
    )
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    (mate,) = results(report, "mate")
    assert mate.status == "unverified" and "fit not measurable" in mate.detail and "mesh" in mate.detail
    assert {p.name: p.backend for p in report.parts} == {"plate": "build123d", "knob": "blender"}
    assert not (tmp_path / "out" / "model.step").exists()  # an assembly with a mesh part has no B-rep STEP
    assert (tmp_path / "out" / "parts" / "knob.glb").is_file()


def test_fixtures_are_valid_design_files() -> None:
    from .conftest import DESIGNS

    for folder in DESIGNS.iterdir():
        DesignFolder(folder).load()
    shutil.rmtree(DESIGNS / "__pycache__", ignore_errors=True)
