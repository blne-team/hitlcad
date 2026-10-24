"""Real builds through the pipeline: the reference designs verify as designed, and seeded faults are caught by
the analysis that owns them."""

from __future__ import annotations

import csv
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from hitlcad.app import HitlCad
from hitlcad.build import assemble
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
    assert not (out / "model.blend").exists()  # no Blender part
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
    assert (tmp_path / "out" / "model.blend").is_file()  # the B-rep plate comes into it from its STL


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
    import subprocess
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
    view = json.loads((out / "view.json").read_text())
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


@pytest.mark.parametrize("name", sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir()))
async def test_the_readme_examples_build_and_verify(core: HitlCad, tmp_path: Path, name: str) -> None:
    folder = shutil.copytree(EXAMPLES / name, tmp_path / name)
    report = await build(core, folder, tmp_path / "out")
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
    view = json.loads((out / "view.json").read_text())
    (leaf,) = view["shapes"]["parts"]
    buffer = view["instances"][leaf["shape"]["ref"]]["vertices"]
    vertices = np.frombuffer(base64.b64decode(buffer["buffer"]), dtype=buffer["dtype"]).reshape(-1, 3)
    placed = trimesh.transform_points(vertices, assemble._frame(leaf["loc"]))
    np.testing.assert_allclose([placed.min(0), placed.max(0)], trimesh.load(out / "mesh.stl").bounds, atol=1e-3)
