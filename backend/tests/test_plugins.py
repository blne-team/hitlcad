"""Plugins: hitlcad's own and an installed one (examples/plugins/hitlcad-lasercut) load the same way, and a plugin
that does not load, clashes or fails says so without taking the rest down."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from hitlcad.agent import describe
from hitlcad.agent.skills import render
from hitlcad.app import HitlCad
from hitlcad.core.checks import Process, ProcessFinding
from hitlcad.core.design import DesignSpec
from hitlcad.kernel import plugins
from hitlcad.kernel.errors import HitlcadError, catalog
from hitlcad.kernel.plugins import Registry, hookimpl, registry
from hitlcad.workspace.document import Scanned
from hitlcad.workspace.folder import DesignFolder

from .conftest import write_design

PLATE = {
    "design.json": {
        "hitlcad": 2, "name": "plate", "backend": "build123d", "ground": "plate",
        "plugins": {"hitlcad-lasercut": ">=0.1"},
        "parameters": [{"name": "sheet", "value": 3, "min": 1, "max": 10}],
        "manufacturing": {"process": "laser_cut", "thickness_mm": 3},
        "parts": {"plate": {"role": "a cut plate", "file": "parts/plate.py", "material": "PETG",
                            "connectors": {"holes": {"kind": "hole", "count": 2}}}},
    },
    "parts/plate.py": "from hitlcad_lasercut.blocks import plate\n\n\ndef build(p, i):\n"
                      "    return {'plate': plate(60, 30, p['sheet'], holes=[(-20, 0), (20, 0)], hole_diameter=2)}\n",
    "assembly.py": "from build123d import Pos\n\n\ndef build(p, i):\n    return {'plate': [Pos()]}\n",
}  # fmt: skip


def test_hitlcad_s_own_plugins_and_an_installed_one_load_alike() -> None:
    found = registry()
    assert found.failures == []
    own = {p.name for p in found.plugins.values() if p.builtin}
    assert own == {"joints", "gears", "cams", "checks", "processes", "build123d", "blender", "catalogs", "exports", "robot",
                   "materials", "solvers", "pictures"}  # fmt: skip
    assert list(found.solvers) == ["sqp"]
    assert found.versions() == {"hitlcad-lasercut": "0.1.0"}
    assert "LaserCutProcess" in [p.__name__ for p in found.processes]
    # its process validates in design.json, its kinds are in the catalog, the skills and the playbook name them
    spec = DesignSpec.model_validate({"name": "x", "backend": "build123d",
                                      "manufacturing": {"process": "laser_cut", "thickness_mm": 2}})  # fmt: skip
    assert spec.manufacturing is not None and spec.manufacturing.allowance("hole") == -0.15
    assert catalog()["manufacturing.laser_holes"].title == "A hole narrower than the sheet is thick"
    assert catalog()["requirement.clearance"].hint.startswith("Move them apart")  # hitlcad's own the same way
    assert '"process": "laser_cut"' in describe.guide()["processes"]
    assert "- `manufacturing.laser_flat`:" in describe.playbook()
    skill = render([])["hitlcad"]
    assert skill["reference/lasercut.md"].startswith("# Laser cutting")
    assert "[lasercut.md](lasercut.md)" in skill["reference/design-json.md"]  # listed beside it, as design.json's


def test_a_design_names_the_plugins_it_needs(tmp_path: Path) -> None:
    folder = write_design(tmp_path / "plate", PLATE)
    assert DesignFolder(folder).load((folder / "design.json").read_text()).plugins == {"hitlcad-lasercut": ">=0.1"}
    for needs, says in (({"hitlcad-lasercut": ">=9"}, "hitlcad-lasercut >=9 is needed, 0.1.0 is installed"),
                        ({"hitlcad-sls": ">=1"}, "hitlcad-sls >=1 is not installed"),
                        ({"hitlcad-lasercut": "soon"}, "'soon' is not a version specifier")):  # fmt: skip
        design = json.loads((folder / "design.json").read_text(encoding="utf-8")) | {"plugins": needs}
        (folder / "design.json").write_text(json.dumps(design), encoding="utf-8")
        with pytest.raises(HitlcadError, match=says) as missing:
            DesignFolder(folder).load((folder / "design.json").read_text())
        assert missing.value.code == "plugin.missing" and missing.value.owner == "plugin"


class _Sls(Process):
    """Selective laser sintering."""

    process: Literal["fdm"]  # the name hitlcad's own FDM process has
    example = {"process": "fdm"}
    findings: dict[str, tuple[str, str]] = {}

    def checks(self, body: Any) -> list[ProcessFinding]:
        return []


def test_two_plugins_giving_one_name_both_lose_it_and_a_broken_one_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise ImportError("no module named sls_kernel")

    real = plugins.entry_points
    fake = SimpleNamespace(name="sls", module="hitlcad_sls", dist=SimpleNamespace(name="hitlcad-sls", version="0.2"),
                           load=broken)  # fmt: skip
    monkeypatch.setattr(plugins, "entry_points", lambda group: [*real(group=group), fake])
    found = Registry()
    assert [f.code for f in found.failures] == ["plugin.load_failed"]
    assert "plugin sls (hitlcad-sls) did not load: ImportError: no module named sls_kernel" in found.failures[0].message

    clash = SimpleNamespace(hitlcad_processes=hookimpl(lambda: [_Sls]))
    found.manager.register(clash, name="clash")
    found.plugins["clash"] = plugins.Plugin(name="clash", module="hitlcad_clash", distribution="hitlcad-clash",
                                            version="1.0", builtin=False)  # fmt: skip
    assert "fdm" not in [plugins._literal(p, "process") for p in found.processes]
    assert found.failures[-1].message == "process 'fdm' is given by hitlcad and hitlcad-clash: neither is used"


@pytest.mark.geometry
async def test_a_plugin_s_process_and_building_block_build_and_its_failures_are_its_own(
    core: HitlCad, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hitlcad_lasercut.process import LaserCutProcess

    folder = write_design(tmp_path / "plate", PLATE)
    spec = DesignFolder(folder).spec(Scanned(), core.settings.tolerances)
    assert spec.plugins == {"hitlcad-lasercut": "0.1.0"}
    report = await core.pipeline.run(spec, folder, tmp_path / "out")  # the part file imports the plugin's block
    assert report.status == "succeeded", report.lead()
    laser = {r.code: r for r in report.results if r.analysis == "manufacturing"}
    assert laser["manufacturing.laser_flat"].status == "pass"
    assert laser["manufacturing.laser_holes"].status == "fail" and "Ø2.00" in laser["manufacturing.laser_holes"].detail

    def broken(self: Any, body: Any) -> Any:
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(LaserCutProcess, "checks", broken)
    thicker = spec.model_copy(update={"overrides": {"sheet": 4}})  # a plate no build has checked yet
    report = await core.pipeline.run(thicker, folder, tmp_path / "again")
    (failed,) = [r for r in report.results if r.analysis == "manufacturing"]
    assert (failed.code, failed.status) == ("plugin.error", "unverified")
    assert failed.detail == ("hitlcad-lasercut 0.1.0: the laser_cut checks of plate: ZeroDivisionError: division by "
                             "zero")  # fmt: skip
    assert report.status == "succeeded" and any(r.analysis == "mate" or r.analysis == "part" for r in report.results)
