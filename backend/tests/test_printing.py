"""Printing: the human's printer, kept in the project, and each printed part against it, lying as it prints."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from hitlcad.app import HitlCad
from hitlcad.builtin.processes.processes import FdmProcess
from hitlcad.core.checks import PrintFacts, PrintOrientation, ProcessFinding
from hitlcad.core.project import Filament, Printer
from hitlcad.kernel.errors import HitlcadError

from .conftest import copy_design, edit_json, write_design

pytestmark = pytest.mark.geometry

CASE = {
    "design.json": {
        "hitlcad": 2, "name": "case", "backend": "build123d", "ground": "plate",
        "manufacturing": {"process": "fdm"},
        "parts": {
            "plate": {"role": "a plate wider than any 256 mm bed, at any angle", "file": "parts/parts.py", "material": "PLA"},
            "tower": {"role": "300 mm tall as modelled, printed lying down", "file": "parts/parts.py", "material": "PETG",
                      "manufacturing": {"process": "fdm", "up": [1, 0, 0]}},
        },
    },
    "parts/parts.py": "from build123d import Align, Box\n\nON = (Align.CENTER, Align.CENTER, Align.MIN)\n\n\n"
                      "def build(p, i):\n    return {'plate': (Box(260, 260, 4, align=ON), {}), "
                      "'tower': (Box(20, 20, 300, align=ON), {})}\n",
    "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'plate': [Pos()], 'tower': [Pos(0, 0, 4)]}\n",
}  # fmt: skip

P2S = Printer(name="Bambu Lab P2S", build_volume_mm=(256, 256, 256), nozzle_mm=0.4, slicer="bambu-studio",
              materials={"PLA": Filament(profile="Bambu PLA Basic", slot=1)})  # fmt: skip


async def test_the_human_s_printer_is_kept_in_the_project_and_each_printed_part_fits_its_bed(
    core: HitlCad, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    write_design(project / "designs" / "case", CASE)
    (project / "hitlcad.toml").write_text("# the human's notes\n[plugins]\n", encoding="utf-8")
    state, _ = core.designs.open(project / "designs" / "case")

    setup = core.printing.setup(state.id)
    assert (setup.file, setup.printer, setup.materials) == (str(project / "hitlcad.toml"), None, ["PETG", "PLA"])
    assert [s.name for s in setup.slicers] == ["bambu-studio"]
    revision = await core.builds.build(state.id)
    assert not [r for r in revision.report.results if r.code == "printing.bed"]  # no printer yet: no bed to fit

    assert core.printing.set_printer(state.id, P2S).printer == P2S
    kept = (project / "hitlcad.toml").read_text(encoding="utf-8")
    assert kept.startswith("# the human's notes\n[plugins]\n") and "[printer.materials.PLA]" in kept
    revision = await core.builds.build(state.id)
    bed = {r.subject: r for r in revision.report.results if r.code == "printing.bed"}
    assert bed["plate: bed"].status == "fail" and "fits on the bed at no angle" in bed["plate: bed"].detail
    assert bed["tower: bed"].status == "pass" and "turned" in bed["tower: bed"].detail  # 300 mm long: only across

    view = revision.report.artifact("print_view")  # the print view: each printed part once, lying as it prints
    assert view is not None
    laid = json.loads((core.designs.store.revision_dir(state.id, revision.id) / view.path).read_text(encoding="utf-8"))
    assert [p["id"] for p in laid["shapes"]["parts"]] == ["/print/plate", "/print/tower"]
    assert laid["shapes"]["bb"]["zmax"] < 30  # the 300 mm tower lies down
    assert [b[2] - b[0] for b in laid["beds"]] == [256, 256]  # each part on a bed of the printer's
    shading = revision.report.artifact("print_shading")  # what it shades, where the print view lays each part
    assert shading is not None
    shades = json.loads(
        (core.designs.store.revision_dir(state.id, revision.id) / shading.path).read_text(encoding="utf-8")
    )
    ways = {part["path"]: part["ways"] for part in shades["parts"]}  # each way each part could lie
    now = {path: next(w for w in found if w["current"]) for path, found in ways.items()}
    assert [list(w) for w in now.values()] == [["up", "current", "move", "bed"]] * 2  # flat boxes: nothing on nothing
    assert now["tower"]["move"] == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]  # where it lies now: no move

    def area(triangles: list[float]) -> float:
        corners = np.reshape(triangles, (-1, 3, 3))
        assert np.allclose(corners[:, :, 2], 0)  # on the bed, where the view lays it
        return float(
            np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum() / 2
        )

    assert area(now["plate"]["bed"] + now["tower"]["bed"]) == pytest.approx(260 * 260 + 300 * 20, rel=1e-3)
    standing = next(w for w in ways["tower"] if np.allclose(w["up"], (0, 0, 1)))  # as modelled: on its 20 mm end
    assert area(standing["bed"]) == pytest.approx(20 * 20, rel=1e-3) and standing["move"] != now["tower"]["move"]
    plan = {p.path: p for p in revision.report.print_plan}  # each printed part once, how many, which way, settings
    assert (plan["plate"].count, plan["plate"].material, next(w.label for w in plan["tower"].ways if w.current)) == (
        1,
        "PLA",
        "on its -X face",
    )
    assert [s.text for s in plan["plate"].settings] == ["0.2 mm layers", "2 walls", "15 % infill", "no supports",
                                                        "no brim"]  # fmt: skip
    assert {s.name: (s.value, s.decided) for s in plan["plate"].settings} == {
        "layer_mm": (0.2, False), "walls": (2, False), "infill_percent": (15, False), "supports": (False, False),
        "brim": (False, False),
    }  # fmt: skip
    with pytest.raises(HitlcadError, match="no slicer 'cura'"):
        core.printing.set_printer(state.id, P2S.model_copy(update={"slicer": "cura"}))


def test_the_process_recommends_from_what_the_build_found_and_keeps_what_the_design_decided() -> None:
    found = PrintFacts(nozzle_mm=None, tightest_fit_mm=0.1, threaded_into=["lid_screws"], findings=[
        ProcessFinding(name="fdm_overhangs", passed=False, detail="2 overhangs need support; the largest 40 mm²",
                       faces={"support": [3, 4]}),  # what prints on nothing: support holds it
        ProcessFinding(name="fdm_bed_contact", passed=False, detail="20.0 × that tall: stand it on a wider face, or add a brim"),
    ])  # fmt: skip
    got = {s.name: s for s in FdmProcess(process="fdm").recommend(found)}
    assert {n: s.value for n, s in got.items()} == {"layer_mm": 0.12, "walls": 4, "infill_percent": 25, "supports": True,
                                                    "brim": True}  # fmt: skip
    assert not any(s.decided for s in got.values())
    assert "tightest fit leaves 0.10 mm" in got["layer_mm"].why and "lid_screws thread into it" in got["walls"].why
    assert "2 overhangs need support" in got["supports"].why

    judged = FdmProcess(process="fdm", walls=6, supports=False, why="it carries the lid's weight; its overhangs bridge")
    kept = {s.name: s for s in judged.recommend(found)}
    assert (kept["walls"].value, kept["walls"].decided, kept["walls"].why) == (6, True, judged.why)
    assert (kept["supports"].value, kept["supports"].decided) == (False, True)

    plain = {s.name: s.value for s in FdmProcess(process="fdm").recommend(
        PrintFacts(nozzle_mm=0.6, tightest_fit_mm=None, threaded_into=[], findings=[]))}  # fmt: skip
    assert plain == {"layer_mm": 0.3, "walls": 2, "infill_percent": 15, "supports": False, "brim": False}


BRACKET = {
    "design.json": {
        "hitlcad": 2, "name": "bracket", "backend": "build123d", "ground": "bracket",
        "manufacturing": {"process": "fdm"},
        "parts": {"bracket": {"role": "a post with an arm out at its top", "file": "parts/bracket.py", "material": "PLA"}},
    },
    "parts/bracket.py": "from build123d import Align, Box, Pos\n\nON = (Align.CENTER, Align.CENTER, Align.MIN)\n\n\n"
                        "def build(p, i):\n    return {'bracket': (Pos(-18, 0, 0) * Box(4, 20, 40, align=ON) "
                        "+ Pos(0, 0, 36) * Box(40, 20, 4, align=ON), {})}\n",
    "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'bracket': [Pos()]}\n",
}  # fmt: skip


async def test_the_build_names_the_best_way_up_and_turning_a_part_there_writes_it(
    core: HitlCad, tmp_path: Path
) -> None:
    state, _ = core.designs.open(write_design(tmp_path / "bracket", BRACKET))
    revision = await core.builds.build(state.id)
    way = next(r for r in revision.report.results if r.code == "printing.orientation")
    # It stands on its post's 4 mm end as modelled, which decides before the support its arm needs
    assert (
        way.status == "fail"
        and way.severity == "warning"
        and "as modelled, as it prints now, it does not stay put" in way.detail
    )
    assert way.values["best_support_mm2"] < 10 < way.values["support_mm2"]  # its arm overhangs as modelled
    (bracket,) = revision.report.print_plan
    ways = bracket.ways
    now = next(w for w in ways if w.current)
    assert ways[0].support_mm2 < 10 and ways[0].stands and now.up == (0, 0, 1) and now.label == "as modelled"
    assert [w.merit for w in ways] == sorted(w.merit for w in ways)  # judged by its process, best first
    # As modelled it stands on its post's 4 mm end, 10 × as tall: its process says why it is no way to print it
    assert not now.stands and any("as tall as its base is wide" in p for p in now.problems)

    told = core.explore.graph(state.id, None).explore("print")  # what the agent reads to judge it
    assert (
        told.startswith("Print plan of rev 1: 1 piece of 1 part")
        and "bracket ×1: PLA, fdm, as modelled; better on" in told
    )
    assert "0.2 mm layers (recommended: half the 0.4 mm nozzle" in told
    turned = await core.builds.turn(state.id, "bracket", ways[0].up, revision.version)
    written = json.loads((tmp_path / "bracket" / "design.json").read_text(encoding="utf-8"))
    assert written["parts"]["bracket"]["manufacturing"] == {"process": "fdm", "up": list(ways[0].up)}
    assert turned.origin == "orientation_edit"
    assert next(r for r in turned.report.results if r.code == "printing.orientation").status == "pass"


async def test_a_component_s_printed_parts_join_the_plan_times_its_placements(core: HitlCad, tmp_path: Path) -> None:
    write_design(tmp_path / "leg", {
        "design.json": {"hitlcad": 2, "name": "leg", "backend": "build123d", "ground": "foot",
                        "manufacturing": {"process": "fdm"},
                        "parts": {"foot": {"role": "a foot", "file": "parts/foot.py", "material": "PETG"}}},
        "parts/foot.py": "from build123d import Box\n\n\ndef build(p, i):\n    return {'foot': (Box(20, 20, 5), {})}\n",
        "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'foot': [Pos()]}\n",
    })  # fmt: skip
    folder = write_design(tmp_path / "stool", {
        "design.json": {"hitlcad": 2, "name": "stool", "backend": "build123d", "ground": "seat",
                        "manufacturing": {"process": "fdm"}, "components": {"leg": {"design": "../leg"}},
                        "parts": {"seat": {"role": "a seat", "file": "parts/seat.py", "material": "PLA"}}},
        "parts/seat.py": "from build123d import Box\n\n\ndef build(p, i):\n    return {'seat': (Box(80, 80, 5), {})}\n",
        "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n"
                       "    return {'seat': [Pos(0, 0, 40)], 'leg': [Pos(-30, 0, 0), Pos(30, 0, 0)]}\n",
    })  # fmt: skip
    state, _ = core.designs.open(folder)
    revision = await core.builds.build(state.id)
    plan = {p.path: (p.count, p.material) for p in revision.report.print_plan}
    assert plan == {"seat": (1, "PLA"), "leg/foot": (2, "PETG")}


def test_a_way_up_is_better_by_fitting_then_standing_then_support_never_by_height_alone() -> None:
    def way(**kw: object) -> PrintOrientation:
        fields = {"up": (0, 0, 1), "label": "x", "fits": True, "stands": True, "support_mm2": 0.0, "bed_mm2": 100.0,
                  "harmed": [], "height_mm": 10.0, "footprint_mm": (20.0, 10.0), "turn_deg": 0.0, "problems": [],
                  "current": False} | kw  # fmt: skip
        return PrintOrientation.model_validate(fields)

    tilted = way(stands=False, support_mm2=4039, label="tilted")  # the dog's body: less support, on a 7 mm² base
    upright = way(support_mm2=4648, label="upright")
    assert upright.merit < tilted.merit
    assert way(fits=False).merit > way(support_mm2=9000).merit  # off the bed is worse than any support
    assert way(harmed=["bore[0]"]).merit > way(support_mm2=9000).merit  # an oval fit is worse than any support
    assert way(harmed=["bore[0]"]).merit < way(stands=False).merit  # but a part that tips over prints nothing
    assert way(support_mm2=12, height_mm=50).merit == way(support_mm2=8, height_mm=5).merit  # within a facet's noise


async def test_a_fitted_hole_printed_on_its_side_comes_out_of_round_which_the_ways_weigh_after_standing(
    core: HitlCad, tmp_path: Path
) -> None:
    folder = copy_design("hinge", tmp_path / "hinge")
    edit_json(folder / "design.json", lambda d: d["mates"][0].update(fit={"clearance": [0.1, 0.3]}))  # 0.2 mm of band
    state, _ = core.designs.open(folder)
    revision = await core.builds.build(state.id)
    base = next(p for p in revision.report.print_plan if p.path == "base")
    now = next(w for w in base.ways if w.current)
    # Its knuckle's hole runs along X as modelled: on its side its 0.2 mm layers step it 0.4 mm out of round
    assert now.harmed == ["hinge[0]"] and "hinge[0] not round" in now.described()
    stepped = next(r for r in revision.report.results if r.code == "manufacturing.fdm_round" and "base" in r.about)
    assert (
        stepped.status == "fail"
        and "its 0.2 mm layers step it 0.40 mm out of round, its fit lets 0.20" in stepped.detail
    )
    # The ways that stand its hole upright tip over: a part that does not stay put prints nothing, so they come after
    # it, and no way is better; splitting it, or a looser fit, is the design's to do
    upright = [w for w in base.ways if not w.harmed]
    assert (
        upright and all(not w.stands for w in upright) and base.ways[0].current and not any(w.better for w in base.ways)
    )
    assert base.ways.index(upright[0]) > base.ways.index(now)


PEGS = {
    "design.json": {
        "hitlcad": 2, "name": "pegs", "backend": "build123d", "ground": "base", "outputs": ["print"],
        "manufacturing": {"process": "fdm"},
        "parts": {
            "base": {"role": "a block", "file": "parts/parts.py", "material": "PLA"},
            "peg": {"role": "a peg printed on its side, two of them", "file": "parts/parts.py", "material": "PETG",
                    "manufacturing": {"process": "fdm", "up": [1, 0, 0], "walls": 3, "why": "it is a test"}},
        },
    },
    "parts/parts.py": "from build123d import Align, Box\n\nON = (Align.CENTER, Align.CENTER, Align.MIN)\n\n\n"
                      "def build(p, i):\n    return {'base': (Box(40, 30, 8, align=ON), {}), "
                      "'peg': (Box(6, 6, 30, align=ON), {})}\n",
    "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n"
                   "    return {'base': [Pos()], 'peg': [Pos(-10, 0, 8), Pos(10, 0, 8)]}\n",
}  # fmt: skip


async def test_the_file_to_print_from_has_each_copy_lying_as_it_prints_with_its_settings_for_the_slicer(
    core: HitlCad, tmp_path: Path
) -> None:
    import xml.etree.ElementTree as ET
    import zipfile

    import trimesh

    project = tmp_path / "project"
    write_design(project / "designs" / "pegs", PEGS)
    (project / "hitlcad.toml").write_text("[plugins]\n", encoding="utf-8")
    state, _ = core.designs.open(project / "designs" / "pegs")
    revision = await core.builds.build(state.id)  # no printer yet: the file waits on the human's
    (wait,) = [r for r in revision.report.results if r.subject == "outputs: print"]
    assert (wait.status, wait.detail) == ("unverified", "the project has no printer yet: the human enters theirs in "
                                          "the panel's Print tab")  # fmt: skip

    core.printing.set_printer(state.id, P2S)
    revision = await core.builds.build(state.id)
    report = revision.report
    assert not [r for r in report.results if r.subject == "outputs: print"]
    assert {a.path for a in report.artifacts if a.role == "print"} == {"print.3mf", "print-plan.md"}
    root = core.designs.store.revision_dir(state.id, revision.id)
    package = zipfile.ZipFile(root / "print.3mf")
    model = ET.fromstring(package.read("3D/3dmodel.model"))
    ns = {"m": model.tag[1:].split("}")[0]}
    objects = {o.get("id"): o for o in model.iter(f"{{{ns['m']}}}object")}
    items = [i.get("objectid") for i in model.iter(f"{{{ns['m']}}}item")]
    config = ET.fromstring(package.read("Metadata/model_settings.config"))

    # each copy an object of its own, its settings as Bambu Studio keeps an object's, its parts the part's meshes
    settings = {o.get("id"): {m.get("key"): m.get("value") for m in o.findall("metadata")} for o in config}
    assert sorted(settings) == sorted(items) and len(items) == 3
    by_name = {s["name"]: s for s in settings.values()}
    assert set(by_name) == {"base", "peg #1", "peg #2"}
    plan = {p.path: {s.name: s for s in p.settings} for p in report.print_plan}
    assert by_name["peg #1"]["wall_loops"] == "3" and by_name["base"]["wall_loops"] == str(
        int(plan["base"]["walls"].value)
    )
    assert by_name["base"]["sparse_infill_density"] == f"{plan['base']['infill_percent'].value:g}%"
    assert by_name["base"]["layer_height"] == f"{plan['base']['layer_mm'].value:g}"
    assert {s["enable_support"] for s in by_name.values()} == {"0"} and {s["brim_type"] for s in by_name.values()} == {
        "no_brim"
    }
    assert by_name["base"]["extruder"] == "1" and "extruder" not in by_name["peg #1"]  # PETG has no filament entered
    for o in config:
        meshes = {c.get("objectid") for c in objects[o.get("id")].iter(f"{{{ns['m']}}}component")}
        assert {p.get("id") for p in o.findall("part")} == meshes

    # lying as it prints, on the bed: the pegs on their sides, 6 mm tall
    scene = trimesh.load(root / "print.3mf", force="scene")
    tall = sorted(round(float(g.bounds[1][2] - g.bounds[0][2]), 3) for g in scene.dump())
    assert tall == [6.0, 6.0, 8.0] and abs(scene.bounds[0][2]) < 1e-6

    page = (root / "print-plan.md").read_text(encoding="utf-8")
    assert page.startswith("# Printing pegs on the Bambu Lab P2S") and "**File › Open Project…**" in page
    assert (
        "| peg | 2 | on its -X face | PETG: no filament entered for it in the Print tab (filament 1) | 0.2 mm layers, 3 walls,"
        in page
    )
    assert "- **3 walls**: it is a test" in page and "PLA: Bambu PLA Basic, AMS slot 1" in page
