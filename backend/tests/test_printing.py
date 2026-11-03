"""Printing: the human's printer, kept in the project, and each printed part against it, lying as it prints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hitlcad.app import HitlCad
from hitlcad.builtin.processes.processes import FdmProcess
from hitlcad.core.checks import PrintFacts, ProcessFinding
from hitlcad.core.project import Filament, Printer
from hitlcad.kernel.errors import HitlcadError

from .conftest import write_design

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
    assert not [r for r in revision.report.results if r.analysis == "printing"]  # no printer yet: nothing to fit

    assert core.printing.set_printer(state.id, P2S).printer == P2S
    kept = (project / "hitlcad.toml").read_text(encoding="utf-8")
    assert kept.startswith("# the human's notes\n[plugins]\n") and "[printer.materials.PLA]" in kept
    revision = await core.builds.build(state.id)
    bed = {r.subject: r for r in revision.report.results if r.analysis == "printing"}
    assert bed["plate: bed"].status == "fail" and "fits on the bed at no angle" in bed["plate: bed"].detail
    assert bed["tower: bed"].status == "pass" and "turned" in bed["tower: bed"].detail  # 300 mm long: only across

    view = revision.report.artifact("print_view")  # the print view: each printed part once, lying as it prints
    assert view is not None
    laid = json.loads((core.designs.store.revision_dir(state.id, revision.id) / view.path).read_text(encoding="utf-8"))
    assert [p["id"] for p in laid["shapes"]["parts"]] == ["/print/plate", "/print/tower"]
    assert laid["shapes"]["bb"]["zmax"] < 30  # the 300 mm tower lies down
    plate = next(p for p in revision.report.parts if p.name == "plate")
    assert {s.name: (s.value, s.decided) for s in plate.print} == {
        "layer_mm": (0.2, False), "walls": (2, False), "infill_percent": (15, False), "supports": (False, False),
        "brim": (False, False),
    }  # fmt: skip
    with pytest.raises(HitlcadError, match="no slicer 'cura'"):
        core.printing.set_printer(state.id, P2S.model_copy(update={"slicer": "cura"}))


def test_the_process_recommends_from_what_the_build_found_and_keeps_what_the_design_decided() -> None:
    found = PrintFacts(nozzle_mm=None, tightest_fit_mm=0.1, threaded_into=["lid_screws"], findings=[
        ProcessFinding(name="fdm_overhangs", passed=False, detail="2 overhangs need support; the largest 40 mm²"),
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
