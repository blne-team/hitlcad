"""The design library must survive installation and teach code that actually builds."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from hitlcad.agent.skills import install
from hitlcad.app import HitlCad
from hitlcad.workspace.document import Scanned
from hitlcad.workspace.folder import DesignFolder

DESIGN_SKILLS = ("hitlcad-design", "hitlcad-form", "hitlcad-dfm", "hitlcad-style")


def test_installed_skills_keep_their_reference_and_asset_links(core: HitlCad, tmp_path: Path) -> None:
    install(tmp_path, core.registry.all())
    for client in (".claude", ".agents"):
        root = tmp_path / client / "skills"
        for name in DESIGN_SKILLS:
            assert (root / name / "SKILL.md").is_file()
        for document in root.glob("hitlcad*/**/*.md"):  # the operating skill's references and playbook too
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                if "://" not in target and not target.startswith("#"):
                    assert (document.parent / target.split("#", 1)[0]).is_file(), (document, target)
        assert (root / "hitlcad-design/assets/service-enclosure/parts/hardware.py").is_file()
        assert (root / "hitlcad-form/assets/section-form/parts/form.py").is_file()
    assert install(tmp_path, core.registry.all()) == []


@pytest.mark.geometry
@pytest.mark.examples
@pytest.mark.parametrize(
    ("skill", "example", "configuration", "expected_height"),
    [
        ("hitlcad-design", "service-enclosure", None, 24),
        ("hitlcad-design", "service-enclosure", "compact", 16),
        ("hitlcad-design", "service-enclosure", "wide", 32),
        ("hitlcad-form", "section-form", None, 60),
        ("hitlcad-form", "section-form", "tall", 80),
        ("hitlcad-design", "mounting-plate", None, 9),
        ("hitlcad-design", "mounting-plate", "compact", 7),
    ],
)
async def test_installed_teaching_designs_build_and_verify(
    core: HitlCad,
    tmp_path: Path,
    skill: str,
    example: str,
    configuration: str | None,
    expected_height: float,
) -> None:
    install(tmp_path / "project", core.registry.all())
    source = tmp_path / "project" / ".agents" / "skills" / skill / "assets" / example
    folder = Path(shutil.copytree(source, tmp_path / "design"))
    spec = DesignFolder(folder).spec(Scanned(), core.settings.tolerances, configuration)
    report = await core.pipeline.run(spec, folder, tmp_path / "out")
    assert report.status == "succeeded", report.lead()
    assert not report.failing(), report.lead()
    parts = {part.name: part for part in report.parts}
    if example == "service-enclosure":
        assert set(parts) == {"base", "lid", "insert", "screw"}
        assert parts["base"].metrics.size[2] == pytest.approx(expected_height)
        fasteners = [result for result in report.results if result.analysis == "fastener"]
        assert len(fasteners) == 4 and all(result.status == "pass" for result in fasteners)
        assert (tmp_path / "out/model.step").is_file()
    elif example == "section-form":
        assert parts["form"].metrics.size[2] == pytest.approx(expected_height)
        assert parts["form"].metrics.watertight
        assert parts["form"].metrics.bodies == 1
        assert (tmp_path / "out/model.blend").is_file()
    else:
        plate = parts["plate"]
        assert plate.metrics.size[2] == pytest.approx(expected_height)
        assert plate.metrics.bodies == 1 and plate.metrics.watertight
        holes = [r for r in report.results if r.code == "connector.feature"]
        assert len(holes) == 4 and all(r.status == "pass" for r in holes)
        assert all(r.values["diameter"] == pytest.approx(3.4) for r in holes)
        assert (tmp_path / "out/model.step").is_file()
