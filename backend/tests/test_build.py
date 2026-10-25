"""Real builds through the pipeline: the reference designs verify as designed, and seeded faults are caught by
the analysis that owns them."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from hitlcad.analysis import cadview
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
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def edit_json(path: Path, change: Any) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def test_the_enclosure_verifies_and_exports(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    assert failing(report) == []
    fasteners = results(report, "fastener", "pass")
    assert len(fasteners) == 4 and all("engages 5.00 mm" in r.detail for r in fasteners)
    assert not [r for r in results(report, "connector") if r.subject.startswith("base.inserts")]  # datums: no result
    assert len(report.instances) == 10 and report.mass and report.mass.mass_g > 20
    out = tmp_path / "out"
    for rel in ("model.step", "mesh.stl", "view.json", "parts/base.step", "parts/lid.3mf", "model.3mf", "bom.csv"):
        assert (out / rel).is_file(), rel
    assert not (out / "model.blend").exists()  # no Blender part
    # A part's glTF carries names and colours; not the PBR texture maps bd_warehouse gives its fasteners.
    assert b'"images"' not in (out / "parts/insert.glb").read_bytes()
    rows = list(csv.DictReader((out / "bom.csv").open()))
    assert {r["name"]: int(r["quantity"]) for r in rows} == {"base": 1, "lid": 1, "insert": 4, "screw": 4}
    urdf = ET.parse(next((out / "urdf").glob("*.urdf"))).getroot()
    assert len(urdf.findall("link")) == 10 and all(j.get("type") == "fixed" for j in urdf.findall("joint"))
    assert ET.parse(out / "sdf" / "model.sdf").getroot().find("model") is not None
    view = json.loads((out / "view.json").read_text(encoding="utf-8"))
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
    assert "bottoms out" not in text  # the lid moved: the screw reaches 5.80 mm into a pocket 6.51 deep
    assert "not seated on lid.screws[0]" in text
    assert "interference base+insert[0]: base and insert[0] overlap" in text


async def test_a_screw_bottoms_out_where_something_stops_it_not_at_the_end_of_its_insert(
    core: HitlCad, tmp_path: Path
) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit(folder / "parts/hardware.py", '"M3-0.5", 9,', '"M3-0.5", 12,')  # 8 mm into a pocket 6.51 deep
    report = await build(core, folder, tmp_path / "short")
    assert any("bottoms out: it reaches 8.00 mm past the opening of insert[0].thread, where base stops it at 6.51"
               in f for f in failing(report))  # fmt: skip
    edit(folder / "parts/box.py", "InsertHole(ins, depth=6)", "InsertHole(ins, depth=12)")  # an open insert, room below
    report = await build(core, folder, tmp_path / "deep")
    assert failing(report) == []
    assert all("engages 5.70 mm" in r.detail for r in results(report, "fastener"))  # the insert's whole thread


async def test_a_fastener_needs_the_into_thread_and_an_iso_273_clearance_hole(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit(folder / "interfaces.py", '"M3-0.5-Standard"', '"M5-0.8-Standard"')  # M5 inserts for the M3 screws
    edit_json(folder / "design.json", lambda d: d["manufacturing"].update(hole_allowance_mm=0.3))  # Ø3.4 prints Ø3.1
    report = await build(core, folder, tmp_path / "out")
    text = "\n".join(r.detail for r in results(report, "fastener", "fail"))
    assert "its M3-0.5 thread does not fit the M5-0.8 thread of insert[0].thread" in text
    assert "the M3-0.5 shank does not clear lid.screws[0]: Ø3.100 as made, ISO 273 asks Ø3.2 (fine)" in text


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
        folder / "parts/hardware.py", "def build(p, i):\n", "def build(p, i):\n    raise ValueError('没有这种嵌件 ✗')\n"
    )
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "failed"
    assert "parts/hardware.py line 5, in build" in report.error and "ValueError: 没有这种嵌件 ✗" in report.error
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


async def test_requirements_follow_the_parameters_they_name(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit_json(folder / "design.json", lambda d: d["requirements"].extend([
        {"text": "as wide as its width", "check": {"kind": "size", "axis": "x", "min": "width", "max": "width"}},
        {"text": "named wrong", "check": {"kind": "wall", "min": "thickness"}},
    ]))  # fmt: skip
    for configuration, width in ((None, 40), ("wide", 60)):
        report = await build(core, folder, tmp_path / f"out-{width}", configuration)
        (follows,) = [r for r in results(report, "requirement") if r.subject == "as wide as its width"]
        assert follows.status == "pass" and f"X {width}.00 mm" in follows.detail, follows.detail
        (wrong,) = [r for r in results(report, "requirement") if r.subject == "named wrong"]
        assert wrong.status == "fail" and "thickness" in wrong.detail, wrong.detail


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
    assert motion.status == "fail" and "lid hits base at -99.0° on the way to -100°" in motion.detail
    assert "make them [-98.9, 0]" in motion.detail  # the limit to change, to where it last moved clear
    (sweep,) = report.motions  # what the panel moves: the lid, about the hinge axis, -100 … 0°
    assert (sweep.kind, sweep.start, sweep.end, sweep.moving) == ("angle", -100, 0, ["lid"])
    assert sweep.axis == pytest.approx((1, 0, 0)) and sweep.current == pytest.approx(0, abs=1e-6)
    edit_json(folder / "design.json", lambda d: d["parameters"][6].update(value=0))  # no stop post
    clear = await build(core, folder, tmp_path / "b")
    (motion,) = results(clear, "motion")
    assert motion.status == "pass" and "-100 … 0°" in motion.detail


PADDLE = {
    "design.json": {
        "hitlcad": 2, "name": "Paddle", "backend": "build123d", "ground": "base",
        "parameters": [{"name": "pin_deg", "value": 7.5, "min": 0, "max": 90},
                       {"name": "pin_h", "value": 4, "min": 0.5, "max": 6}],
        "parts": {"base": {"role": "a plate with a pin", "file": "parts/paddle.py", "material": "PLA",
                           "connectors": {"axle": {"kind": "frame"}}},
                  "paddle": {"role": "turns about the axle", "file": "parts/paddle.py", "material": "PLA",
                             "connectors": {"hub": {"kind": "frame"}}}},
        "mates": [{"type": "revolute", "a": "base.axle", "b": "paddle.hub"}],
        "requirements": [{"text": "the paddle clears the plate",
                          "check": {"kind": "clearance", "between": ["paddle", "base"], "min": 0.3}}],
    },
    # A 1 mm wide paddle 1.5 mm over a plate, turning past a Ø1 pin 18 mm out from its axle
    "parts/paddle.py": """import math

from build123d import Align, Box, Cylinder, Pos, Rot

LOW = (Align.CENTER, Align.CENTER, Align.MIN)


def build(p, i):
    a = math.radians(p["pin_deg"])
    base = Box(50, 50, 4, align=LOW) + Pos(18 * math.cos(a), 18 * math.sin(a), 4) * Cylinder(0.5, p["pin_h"], align=LOW)
    paddle = Pos(12, 0, 0.5) * Box(20, 1, 2, align=LOW)
    return {"base": (base, {"axle": Pos(0, 0, 5)}), "paddle": (paddle, {"hub": Rot(180, 0, 0)})}
""",
    "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'base': [Pos()], 'paddle': [Pos(0, 0, 5)]}\n",
}  # fmt: skip


async def test_motion_finds_what_the_paddle_meets_between_any_two_positions(core: HitlCad, tmp_path: Path) -> None:
    folder = write_design(tmp_path / "paddle", PADDLE)
    # The pin stands in the paddle's way only from 4.3° to 10.7°: sampling a turn every 15° never meets it
    report = await build(core, folder, tmp_path / "a")
    (motion,) = results(report, "motion")
    assert motion.status == "fail" and "paddle hits base at 4.3° on the way to 180°" in motion.detail, motion.detail
    assert 4.25 < motion.values["angle"] < 4.35
    (clear,) = results(report, "requirement")  # measured over the turn, not only where it sits
    assert clear.status == "fail" and "0.00 mm (needs ≥ 0.3) at 4.3° of base.axle+paddle.hub" in clear.detail

    # Lower, the pin passes 0.2 mm under it: nothing collides, but the clearance is broken half way round
    edit_json(folder / "design.json", lambda d: d["parameters"][1].update(value=1.3))
    report = await build(core, folder, tmp_path / "b")
    (motion,) = results(report, "motion")
    assert motion.status == "pass" and "moves through a full turn without collision" in motion.detail
    (clear,) = results(report, "requirement")
    assert clear.status == "fail" and clear.detail.startswith("paddle to base: 0.20 mm (needs ≥ 0.3) at "), clear.detail

    # Under it where it sits: interference says where and how deep; motion does not fail it a second time
    edit_json(
        folder / "design.json", lambda d: (d["parameters"][0].update(value=0), d["parameters"][1].update(value=4))
    )
    report = await build(core, folder, tmp_path / "c")
    (overlap,) = results(report, "interference", "fail")
    assert "base and paddle overlap by 1.5" in overlap.detail and "in 1 piece within x 17.50 … 18.50" in overlap.detail
    assert "z 5.50 … 7.50, about 1.00 mm deep" in overlap.detail, overlap.detail
    (motion,) = results(report, "motion")
    assert motion.status == "unverified" and "paddle and base already overlap by 1.5" in motion.detail


async def test_a_turn_past_a_part_that_cannot_be_measured_is_not_called_clear(core: HitlCad, tmp_path: Path) -> None:
    # A Blender cube with a face deleted, in the paddle's way, and the pin low enough to pass under it
    post = "import bmesh\nimport bpy\n\n\ndef build(p, i):\n    bpy.ops.mesh.primitive_cube_add(size=1, location=(18, 3, 6))\n" \
        "    post = bpy.context.active_object\n    mesh = bmesh.new()\n    mesh.from_mesh(post.data)\n" \
        "    bmesh.ops.delete(mesh, geom=list(mesh.faces)[:1], context='FACES_ONLY')\n    mesh.to_mesh(post.data)\n" \
        "    return {'post': (post, {})}\n"  # fmt: skip
    folder = write_design(tmp_path / "paddle", {**PADDLE, "parts/post.py": post})
    edit_json(folder / "design.json", lambda d: (d["parameters"][1].update(value=1.3), d["parts"].update(
        post={"role": "in the way", "file": "parts/post.py", "backend": "blender", "material": "PLA"})))  # fmt: skip
    edit(folder / "assembly.py", "'paddle': [Pos(0, 0, 5)]", "'paddle': [Pos(0, 0, 5)], 'post': [Pos()]")
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    (motion,) = results(report, "motion")
    assert motion.status == "unverified" and "paddle against post is not measurable" in motion.detail, motion.detail


async def test_an_insert_set_into_solid_material_is_not_an_expected_overlap(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    edit(folder / "parts/box.py", "InsertHole(ins, depth=6)", "InsertHole(ins, depth=6, mode=Mode.PRIVATE)")  # no hole
    report = await build(core, folder, tmp_path / "out")
    inserts = [r for r in results(report, "interference", "fail") if "+insert[" in r.subject]
    assert len(inserts) == 4 and all("the hole is not cut there" in r.detail for r in inserts), failing(report)


async def test_the_fan_turns_into_a_pin_in_its_duct_and_one_too_big_for_it_once(core: HitlCad, tmp_path: Path) -> None:
    folder = shutil.copytree(EXAMPLES / "handhold-fan", tmp_path / "fan")
    # A Ø1.2 pin into the blades' path at 22.5°: eight blades, every 45°, meet it only between positions
    edit_json(folder / "design.json", lambda d: next(p for p in d["parameters"] if p["name"] == "blade_count")
              .update(value=8))  # fmt: skip
    wire = 'add(teardrop(pad, p["wire_hole_d"], i.pad_depth), mode=Mode.SUBTRACT)\n'
    edit(folder / "parts/duct.py", wire, wire + "        add(Rot(0, 0, 22.5) * Pos(33.25, 0, 13) * Rot(0, 90, 0) * "
         "Cylinder(0.6, 4.5, mode=Mode.PRIVATE))\n")  # fmt: skip
    edit(folder / "parts/duct.py", "Pos, add", "Pos, Rot, add")
    report = await build(core, folder, tmp_path / "pin")
    assert report.status == "succeeded", report.error
    (motion,) = [r for r in results(report, "motion") if r.subject == "motor.shaft+impeller.bore"]
    assert motion.status == "fail" and "impeller hits duct at 18.5°" in motion.detail and "at -18.5°" in motion.detail
    (tips,) = [r for r in results(report, "requirement") if r.subject == "Impeller blade tips clear the duct"]
    assert tips.status == "fail" and "0.74 mm where they sit" in tips.detail, tips.detail
    # An impeller 0.5 mm too big for its duct overlaps it where it sits: interference says so, once
    shutil.rmtree(folder)
    folder = shutil.copytree(EXAMPLES / "handhold-fan", tmp_path / "fan")
    edit(folder / "parts/impeller.py", 'p["fan_d"] / 2', "70 / 2")
    report = await build(core, folder, tmp_path / "big")
    (overlap,) = results(report, "interference", "fail")
    assert "duct and impeller overlap by 91.1" in overlap.detail and "in 7 pieces" in overlap.detail, overlap.detail
    assert "about 0.2" in overlap.detail
    (motion,) = [r for r in results(report, "motion") if r.subject == "motor.shaft+impeller.bore"]
    assert motion.status == "unverified" and "already overlap" in motion.detail


PRESS = {
    "design.json": {
        "hitlcad": 2, "name": "Press", "backend": "build123d", "ground": "plate",
        "parameters": [{"name": "sink", "value": 0, "min": 0, "max": 1}],
        "parts": {"plate": {"role": "holds the pin", "file": "parts/press.py", "material": "PLA",
                            "connectors": {"hole": {"kind": "hole"}}},
                  "pin": {"role": "pressed in", "file": "parts/press.py", "material": "PLA",
                          "connectors": {"shaft": {"kind": "shaft"}}}},
        "mates": [{"type": "rigid", "a": "plate.hole", "b": "pin.shaft", "fit": {"clearance": [-0.1, 0]}}],
    },
    # A Ø3.05 pin with a Ø6 head pressed into a Ø3 hole through a 5 mm plate
    "parts/press.py": """from build123d import Align, Cylinder, Box, Pos, Rot

LOW, HIGH = (Align.CENTER, Align.CENTER, Align.MIN), (Align.CENTER, Align.CENTER, Align.MAX)


def build(p, i):
    plate = Box(20, 20, 5, align=LOW) - Cylinder(1.5, 5, align=LOW)
    pin = Cylinder(1.525, 8, align=HIGH) + Cylinder(3, 2, align=LOW)
    return {"plate": (plate, {"hole": Pos(0, 0, 5)}), "pin": (pin, {"shaft": Rot(180, 0, 0)})}
""",
    "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'plate': [Pos()], 'pin': [Pos(0, 0, 5 - p['sink'])]}\n",
}  # fmt: skip


async def test_an_interference_fit_allows_the_ring_it_predicts_and_no_more(core: HitlCad, tmp_path: Path) -> None:
    folder = write_design(tmp_path / "press", PRESS)
    report = await build(core, folder, tmp_path / "a")
    (fit,) = results(report, "interference", "pass")[:1]
    assert fit.detail.startswith("overlap 1.19 mm³, expected: the plate.hole+pin.shaft fit"), fit.detail
    edit_json(folder / "design.json", lambda d: d["parameters"][0].update(value=0.5))  # the head sunk into the plate
    report = await build(core, folder, tmp_path / "b")
    (beyond,) = results(report, "interference", "fail")
    assert "more than the 1.66 mm³ the plate.hole+pin.shaft interference fit makes" in beyond.detail, beyond.detail


ISO_FITS = [  # pin Ø, diametral gap modelled, fit, passes, what the mate says
    (2, 0, "H7/h6", True, "fit H7/h6 at Ø2: hole +0.010/0, shaft 0/-0.006, clearance 0.000 … 0.016 mm"),
    (3, 0, "H7/h6", True, "fit H7/h6 at Ø3: hole +0.010/0"),  # the 3 mm bound belongs to the range up to 3
    (8, 0, "H7/r6", False, "a cylindrical mate moves, but its fit leaves no room: greatest clearance -0.004 mm"),
    (8, 0.03, "H7/g6", False, "fit H7/g6: the hole and shaft differ by 0.030 mm"),  # modelled at the fit's limits
    (3, 0, "H7/t6", False, "fit H7/t6: ISO 286 does not define t6 at Ø3, only above 24 up to 3150 mm"),
]


@pytest.mark.parametrize(
    ("pin_d", "gap", "fit", "passes", "says"), ISO_FITS, ids=[f"{f}@{d}+{g}" for d, g, f, *_ in ISO_FITS]
)
async def test_iso_fits_hold_over_the_standard_s_range_and_leave_a_moving_mate_room(
    core: HitlCad, tmp_path: Path, pin_d: float, gap: float, fit: str, passes: bool, says: str
) -> None:
    folder = copy_design("hinge", tmp_path / "hinge")
    edit_json(folder / "design.json", lambda d: (
        d["parameters"][3].update(value=pin_d, max=10), d["parameters"][4].update(value=gap),
        d["mates"][0].update(fit={"iso": fit})))  # fmt: skip
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    (mate,) = results(report, "mate")
    assert (mate.status == "pass") is passes and says in mate.detail, mate.detail
    # A fit modelled without a gap overlaps by its facets only: neither interference nor motion fails on that
    assert results(report, "interference") and not results(report, "interference", "fail")
    (motion,) = results(report, "motion")
    assert motion.status == "pass" or motion.values["angle"] < -10, motion.detail  # the post stops it, if anything


async def test_an_analysis_that_crashes_is_reported_and_spares_the_others(
    core: HitlCad, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (motion,) = [a for a in core.pipeline.analyses if a.name == "motion"]

    def crash(ctx: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(motion, "run", crash)
    report = await build(core, copy_design("hinge", tmp_path / "hinge"), tmp_path / "out")
    assert report.status == "succeeded"
    (crashed,) = results(report, "motion")
    assert crashed.status == "unverified" and crashed.detail == "the analysis failed: boom"
    assert results(report, "interference")


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
    # The bearing's balls touch its races along edges: still one closed solid, measured like any other
    assert "closed mesh" in [r.detail for r in results(report, "part") if r.subject == "bearing"]
    (sun,) = [r for r in results(report, "connector") if r.subject == "sun.teeth"]
    assert sun.status == "pass" and sun.detail == "external gear m1.25, 18 teeth, 10 mm wide: tip Ø25.000"
    motion = results(report, "motion")  # a planetary is a closed loop: not analysed, never wrongly failed
    assert motion and not [r for r in motion if r.status == "fail"]
    assert any(r.status == "unverified" and "closed loop" in r.detail for r in motion)
    (stack,) = [r for r in results(report, "requirement") if "axial gap" in r.subject]
    assert stack.status == "pass" and stack.detail.startswith(
        "0.500 mm nominal, 0.300 … 0.700 worst case (± 0.200), ± 0.141 RSS"
    )
    ratio_5 = await build(core, folder, tmp_path / "ratio_5", "ratio_5")
    assert ratio_5.status == "succeeded" and len(results(ratio_5, "relation", "pass")) == 6


async def test_a_gear_cut_to_another_module_fails_its_connector_and_meshes_with_nothing(
    core: HitlCad, tmp_path: Path
) -> None:
    folder = copy_design("gearbox", tmp_path / "gearbox")
    edit(folder / "parts/gears.py", 'planet = spur(p["planet_teeth"], m,', 'planet = spur(p["planet_teeth"], 1.0,')
    report = await build(core, folder, tmp_path / "out")
    assert "connector planet.teeth: tip Ø20.000, m1.25 × (18 + 2) needs Ø25.000 ± 0.125" in failing(report)
    unverified = [r.detail for r in results(report, "relation", "unverified")]
    assert len(unverified) == 6 and "not verified until planet[0].teeth measures as declared" in unverified[0]


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
            "parts/plate.py": "from build123d import Box, Color, Cylinder, Location, Align\n\n\ndef build(p, i):\n"
            "    plate = Box(40, 40, 6, align=(Align.CENTER, Align.CENTER, Align.MIN)) - "
            "Cylinder(2, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
            "    plate.color = Color(0, 0.5, 0)\n"
            "    return {'plate': (plate, {'socket': Location((0, 0, 6))})}\n",
            # A sphere with a Ø3.8 stem below it, into the plate's Ø4 hole.
            "parts/knob.py": "import bpy\nfrom mathutils import Matrix\n\n\ndef build(p, i):\n"
            "    bpy.ops.mesh.primitive_uv_sphere_add(radius=p['r'], location=(0, 0, p['r']))\n"
            "    knob = bpy.context.active_object\n    knob.name = 'grip'\n"
            "    bpy.ops.mesh.primitive_cylinder_add(radius=1.9, depth=6.5, location=(0, 0, -2.75))\n"
            "    union = knob.modifiers.new('stem', 'BOOLEAN')\n"
            "    union.operation, union.object, union.solver = 'UNION', bpy.context.active_object, 'EXACT'\n"
            "    return {'knob': (knob, {'stem': Matrix.Rotation(3.14159265, 4, 'X')})}\n",
            "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'plate': [Pos()], 'knob': [Pos(0, 0, 6)]}\n",
        },
    )
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    (stem,) = [r for r in results(report, "connector") if r.subject == "knob.stem"]
    assert stem.status == "pass" and abs(stem.values["diameter"] - 3.8) < 0.05 and abs(stem.values["length"] - 6) < 0.05
    (mate,) = results(report, "mate")
    assert mate.status == "pass" and "on the meshes" in mate.detail, mate.detail
    assert {p.name: p.backend for p in report.parts} == {"plate": "build123d", "knob": "blender"}
    assert not (tmp_path / "out" / "model.step").exists()  # an assembly with a mesh part has no B-rep STEP
    # A B-rep part's colour reaches the view, as a mesh part's does
    view = json.loads((tmp_path / "out" / "view.json").read_text(encoding="utf-8"))
    plate = next(leaf for leaf in cadview.leaves(view["shapes"]) if leaf["id"].endswith("/plate"))
    red, green, blue = (int(plate["color"][i : i + 2], 16) for i in (1, 3, 5))
    assert green > 0 and red == blue == 0, plate["color"]
    # Blender assembles both backends' parts from their glTF, each where mesh.stl has it
    run = subprocess.run([core.settings.blender_binary, "-b", str(tmp_path / "out" / "model.blend"), "--factory-startup",
                          "-noaudio", "--python-expr", BLEND_CHECK], capture_output=True, text=True, check=True)  # fmt: skip
    blend = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("BLEND "))[6:])
    np.testing.assert_allclose(blend["bounds"], trimesh.load(tmp_path / "out" / "mesh.stl").bounds, atol=1e-3)


COLOURED_PARTS = """import bpy


def colour(name, rgb):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1)
    return material


def build(p, i):
    bpy.ops.mesh.primitive_cube_add(size=20, location=(0, 0, 10))
    block = bpy.context.active_object
    block.name = "block"
    block.data.materials.append(colour("red", (1, 0, 0)))
    block.data.materials.append(colour("white", (1, 1, 1)))
    max(block.data.polygons, key=lambda f: f.center.z).material_index = 1
    bpy.ops.mesh.primitive_uv_sphere_add(radius=3, location=(0, 0, 3))
    ball = bpy.context.active_object
    ball.name = "ball"
    ball.data.materials.append(colour("blue", (0, 0, 1)))
    return {"base": (block, {}), "knob": (ball, {})}
"""

COLOURED_ASSEMBLY = """from math import pi

from mathutils import Matrix


def build(p, i):
    return {"base": [Matrix.Identity(4)],
            "knob": [Matrix.Translation((-5, 0, 20)), Matrix.Translation((6, 2, 20)) @ Matrix.Rotation(pi / 2, 4, "X")]}
"""

PEG_PARTS = """import bpy
from mathutils import Matrix


def build(p, i):
    bpy.ops.mesh.primitive_cube_add(size=20, location=(0, 0, 10))
    block = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(radius=2, depth=12, location=(0, 0, 20))
    socket = block.modifiers.new("socket", "BOOLEAN")  # a Ø4 socket, 6 deep from the top
    socket.operation, socket.object, socket.solver = "DIFFERENCE", bpy.context.active_object, "EXACT"
    bpy.ops.mesh.primitive_cylinder_add(radius=p["pin_r"], depth=6, location=(0, 0, 3), vertices=64)
    return {"base": (block, {"socket": Matrix.Translation((0, 0, 20))}),
            "pin": (bpy.context.active_object, {"tip": Matrix.Identity(4)})}
"""

PEG_ASSEMBLY = """from mathutils import Matrix

from hitlcad.sdk import mate_to


def build(p, i):
    return {"base": [Matrix.Identity(4)], "pin": [mate_to(Matrix.Identity(4), Matrix.Translation((0, 0, 20)))]}
"""


@pytest.mark.parametrize(("pin_r", "fits"), [(1.9, True), (2.2, False)])
async def test_blender_parts_are_measured_and_placed_with_the_sdk(
    core: HitlCad, tmp_path: Path, pin_r: float, fits: bool
) -> None:
    folder = write_design(tmp_path / "peg", {
        "design.json": {"hitlcad": 2, "name": "Peg", "backend": "blender", "ground": "base",
                        "parameters": [{"name": "pin_r", "value": pin_r, "min": 1, "max": 3}],
                        "parts": {"base": {"role": "block", "file": "parts/peg.py", "material": "PLA",
                                           "connectors": {"socket": {"kind": "hole"}}},
                                  "pin": {"role": "pin", "file": "parts/peg.py", "material": "PLA",
                                          "connectors": {"tip": {"kind": "shaft"}}}},
                        "mates": [{"type": "cylindrical", "a": "base.socket", "b": "pin.tip",
                                   "fit": {"clearance": [0, 0.3]}}]},
        "parts/peg.py": PEG_PARTS,
        "assembly.py": PEG_ASSEMBLY,
    })  # fmt: skip
    report = await build(core, folder, tmp_path / "out")
    assert report.status == "succeeded", report.error
    socket, tip = (r for r in results(report, "connector"))
    assert socket.values["diameter"] == pytest.approx(4, abs=0.02) and socket.values["length"] == pytest.approx(
        6, abs=0.05
    )
    assert tip.values["diameter"] == pytest.approx(2 * pin_r, abs=0.02) and "on the mesh" in tip.detail
    (mate,) = results(report, "mate")
    assert (mate.status == "pass") is fits, mate.detail
    assert all(r.status == "pass" for r in results(report, "interference")) is fits


# Opens a .blend and prints its collection instances and the world bounds of the geometry they show.
BLEND_CHECK = """import json
import bpy
import numpy as np

points = []
for inst in bpy.context.evaluated_depsgraph_get().object_instances:
    if inst.is_instance and inst.object.type == "MESH":
        co = np.empty(len(inst.object.data.vertices) * 3)
        inst.object.data.vertices.foreach_get("co", co)
        m = np.array(inst.matrix_world)
        points.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
points = np.vstack(points)
print("BLEND", json.dumps({
    "instances": sorted(o.name for o in bpy.data.objects if o.instance_type == "COLLECTION"),
    "bounds": [points.min(0).tolist(), points.max(0).tolist()],
    "materials": sorted(m.name for m in bpy.data.materials),
}))
"""


async def test_blender_colours_reach_the_view_and_3mf_and_blender_assembles_the_design(
    core: HitlCad, tmp_path: Path
) -> None:
    import locale
    import zipfile

    encoding = locale.getpreferredencoding(False)
    folder = write_design(tmp_path / "toy", {
        "design.json": {"hitlcad": 2, "name": "Toy", "backend": "blender", "ground": "base", "outputs": ["3mf"],
                        "parts": {"base": {"role": "red block, white top", "file": "parts/toy.py", "material": "PLA"},
                                  "knob": {"role": "blue ball", "file": "parts/toy.py", "material": "PLA"}}},
        "parts/toy.py": COLOURED_PARTS,
        "assembly.py": COLOURED_ASSEMBLY,
    })  # fmt: skip
    out = tmp_path / "out"
    report = await build(core, folder, out)
    assert report.status == "succeeded", report.error
    red, white, blue = "#ff0000", "#ffffff", "#0000ff"

    # The view: a part in one colour is one shape at its instance; in two, a group of a shape per colour.
    view = json.loads((out / "view.json").read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in view["shapes"]["parts"]}
    assert {leaf["id"]: leaf["color"] for leaf in nodes["/Toy/base"]["parts"]} == {
        "/Toy/base/red": red,
        "/Toy/base/white": white,
    }
    assert nodes["/Toy/knob[0]"]["color"] == nodes["/Toy/knob[1]"]["color"] == blue
    assert len(view["instances"]) == 3  # each colour of each part tessellated once

    # 3MF: each part's colours as base materials, the assembly placed as mesh.stl is.
    def colours(rel: str) -> set[str]:
        model = ET.fromstring(zipfile.ZipFile(out / rel).read("3D/3dmodel.model"))
        return {b.get("displaycolor", "")[:7].lower() for b in model.iter() if b.tag.endswith("}base")}

    assert colours("parts/base.3mf") == {red, white} and colours("parts/knob.3mf") == {blue}
    assert colours("model.3mf") == {red, white, blue}
    mesh_bounds = trimesh.load(out / "mesh.stl").bounds
    np.testing.assert_allclose(trimesh.load(out / "model.3mf", force="scene").bounds, mesh_bounds, atol=1e-3)
    assert locale.getpreferredencoding(False) == encoding  # lib3mf left the process's locale as it found it

    # model.blend: the parts' objects and materials, three collection instances where the assembly put them.
    assert {a.path: a.role for a in report.artifacts}["model.blend"] == "native"
    run = subprocess.run([core.settings.blender_binary, "-b", str(out / "model.blend"), "--factory-startup", "-noaudio",
                          "--python-expr", BLEND_CHECK], capture_output=True, text=True, check=True)  # fmt: skip
    blend = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("BLEND "))[6:])
    assert blend["instances"] == ["Toy/base", "Toy/knob[0]", "Toy/knob[1]"]
    assert blend["materials"] == ["blue", "red", "white"]
    np.testing.assert_allclose(blend["bounds"], mesh_bounds, atol=1e-3)


def test_fixtures_are_valid_design_files() -> None:
    from .conftest import DESIGNS

    for folder in DESIGNS.iterdir():
        DesignFolder(folder).load()
    shutil.rmtree(DESIGNS / "__pycache__", ignore_errors=True)


EXAMPLES = Path(__file__).parents[2] / "examples"


EXAMPLE_BUILDS = [
    (folder.name, configuration)
    for folder in sorted(p for p in EXAMPLES.iterdir() if p.is_dir())
    for configuration in [
        None,
        *json.loads((folder / "design.json").read_text(encoding="utf-8")).get("configurations", {}),
    ]
]


@pytest.mark.parametrize(
    ("name", "configuration"), EXAMPLE_BUILDS, ids=[f"{n}-{c or 'default'}" for n, c in EXAMPLE_BUILDS]
)
async def test_the_readme_examples_build_and_verify_in_every_configuration(
    core: HitlCad, tmp_path: Path, name: str, configuration: str | None
) -> None:
    folder = shutil.copytree(EXAMPLES / name, tmp_path / name)
    report = await build(core, folder, tmp_path / "out", configuration)
    assert report.status == "succeeded", report.error
    assert failing(report) == []


async def test_the_view_places_parts_whose_shape_was_moved_into_their_own_frame(core: HitlCad, tmp_path: Path) -> None:
    import base64

    # Modelled where it sits in the product, then moved into its own frame; the assembly moves it back.
    part = """from build123d import *


def build(p, i):
    return {"block": (Box(20, 10, 4).moved(Location((100, 50, 0))).moved(Location((-100, -50, 0), (0, 0, 90))), {})}
"""
    assembly = """from build123d import *


def build(p, i):
    return {"block": [Location((100, 50, 0)) * Rot(0, 0, -90)]}
"""
    folder = write_design(tmp_path / "moved", {
        "design.json": {"hitlcad": 2, "name": "Moved", "backend": "build123d", "ground": "block",
                        "parts": {"block": {"role": "a block", "file": "parts/block.py", "material": "PLA"}}},
        "parts/block.py": part,
        "assembly.py": assembly,
    })  # fmt: skip
    out = tmp_path / "out"
    report = await build(core, folder, out)
    assert report.status == "succeeded", report.error
    view = json.loads((out / "view.json").read_text(encoding="utf-8"))
    (leaf,) = view["shapes"]["parts"]
    buffer = view["instances"][leaf["shape"]["ref"]]["vertices"]
    vertices = np.frombuffer(base64.b64decode(buffer["buffer"]), dtype=buffer["dtype"]).reshape(-1, 3)
    placed = trimesh.transform_points(vertices, cadview._frame(leaf["loc"]))
    np.testing.assert_allclose([placed.min(0), placed.max(0)], trimesh.load(out / "mesh.stl").bounds, atol=1e-3)
