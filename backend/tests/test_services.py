"""The services the panel and agents use, on real builds: designs, builds, picks and pins across rebuilds, the
design graph, revision comparisons, the MCP tools and project setup."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from hitlcad.analysis.selection import topology
from hitlcad.api.mcp_server import build_mcp
from hitlcad.app import HitlCad
from hitlcad.core.types import ComponentRef, Pick
from hitlcad.designs import agents
from hitlcad.designs.project import init_project
from hitlcad.designs.watcher import _relevant

from .conftest import copy_design

pytestmark = pytest.mark.geometry


def face_index(shape, test) -> int:
    return next(k for k, f in enumerate(shape.faces()) if test(f))


async def test_a_new_design_is_scaffolded_and_waits_for_parts(core: HitlCad, tmp_path: Path) -> None:
    state, created = core.designs.open(tmp_path / "new", "build123d", "New")
    assert created == ["design.json", "interfaces.py", "assembly.py", "parts/.gitkeep"]
    assert json.loads((tmp_path / "new" / "design.json").read_text(encoding="utf-8"))["hitlcad"] == 2
    with pytest.raises(ValueError, match="declares no parts yet"):
        await core.builds.build(state.id)
    assert await core.builds.rebuild(state.id) is None


async def test_picks_name_instances_and_connectors_and_pins_follow_rebuilds(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    state, _ = core.designs.open(folder)
    rev = await core.builds.build(state.id)
    built = core.feedback.built(state.id, rev)
    lid = built.parts["lid"].features.exact()
    top = face_index(lid, lambda f: f.is_planar and f.normal_at().Z > 0.99)
    hole = face_index(lid, lambda f: f.geom_type.name == "CYLINDER")
    (item,) = await core.feedback.describe_picks(state.id, None, [Pick(point=(0, 0, 24), component=ComponentRef(
        instance="lid", topo="face", index=hole))])  # fmt: skip
    assert item.connector and item.connector.startswith("lid.screws[") and item.label.startswith(item.connector)
    pin = await core.feedback.add_pin(state.id, Pick(point=(0, 0, 24), component=ComponentRef(
        instance="lid", topo="face", index=top)), "make it thicker")  # fmt: skip
    assert pin.instance == "lid" and "face" in pin.label
    rebuilt = await core.builds.edit_parameters(state.id, {"lid_t": 6})
    (carried,) = core.designs.get(state.id).open_pins
    assert carried.revision == rebuilt.id and carried.point[2] == pytest.approx(26)
    new_top = topology(core.feedback.built(state.id, rebuilt).parts["lid"].features, carried.component)
    assert new_top.is_planar and new_top.normal_at().Z > 0.99
    saved = json.loads((folder / "design.json").read_text(encoding="utf-8"))
    assert next(p["value"] for p in saved["parameters"] if p["name"] == "lid_t") == 6


async def test_the_design_graph_answers_with_addresses_and_impact(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    state, _ = core.designs.open(folder)
    await core.builds.build(state.id)
    graph = core.explore.graph(state.id)
    overview = graph.explore()
    assert (
        "2 made + 2 catalog parts" in overview
        and "10 instances" in overview
        and "catalog: insert ×4, screw ×4" in overview
    )
    lid = graph.explore("lid")
    assert "parts/box.py" in lid and "reads: height, lid_t, wall, width, interfaces.corners" in lid
    assert "screws (hole ×4: Ø3.400 × 4.00 deep)" in lid and "fastener set screw.seat:lid.screws>insert.thread" in lid
    width = graph.explore("width")
    assert (
        "interface values: interfaces.corners" in width and "read by: base, lid and assembly.py (placements)" in width
    )
    assert "lid.screws" in graph.explore("lid.screws") and "screw[2]" in graph.explore("screw[2]")
    (folder / "interfaces.py").write_text(
        (folder / "interfaces.py").read_text(encoding="utf-8") + "\n\ndef extra(p, i):\n    return 1\n",
        encoding="utf-8",
    )
    assert "stale: changed since rev 1: interfaces.py" in core.explore.graph(state.id).explore()
    (folder / "design.json").write_text("{", encoding="utf-8")  # broken mid-edit: the graph says so, not rev 1
    assert "✗ design.json does not load, so this is rev 1's:" in core.explore.graph(state.id).explore()
    assert graph.structure()["nodes"][0] == {
        "id": "base",
        "kind": "made",
        "label": "base",
        "role": "open box",
        "count": 1,
        "status": "pass",
    }


async def test_revisions_compare_and_designs_using_a_design_rebuild(core: HitlCad, tmp_path: Path) -> None:
    copy_design("hinge", tmp_path / "hinge")
    copy_design("shelf", tmp_path / "shelf")
    hinge, _ = core.designs.open(tmp_path / "hinge")
    shelf, _ = core.designs.open(tmp_path / "shelf")
    assert core.designs.where_used(hinge.id) == [shelf.id]
    first = await core.builds.build(hinge.id)
    second = await core.builds.edit_parameters(hinge.id, {"stop_h": 0})
    text = core.explore.compare(hinge.id, first.id, second.id)
    assert "stop_h: 30.0 → 0.0" in text and "motion base.hinge+lid.pin: fail → pass" in text
    assert "Parts rebuilt in 2: base, lid" in text


async def test_the_mcp_tools(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    path = str(folder)
    async with Client(build_mcp(core)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"list_backends", "create_design", "search_catalog", "build_design", "explore_design",
                         "compare_revisions", "list_geometry", "measure_geometry", "resolve_pins"}  # fmt: skip

        async def call(name: str, **args: object) -> str:
            result = await client.call_tool(name, args)
            assert not result.is_error, result.content[0].text
            return result.content[0].text

        assert "every reference resolves" in await call("build_design", path=path, scope="structure")
        assert "placed: base, lid, insert ×4, screw ×4" in await call("build_design", path=path, scope="structure")
        built = await call("build_design", path=path)
        assert "SUCCEEDED" in built and "no problems; 34 checks pass" in built and "build/" in built
        scoped = await call("build_design", path=path, scope="lid")
        assert "Scoped to lid:" in scoped
        catalog = await call("search_catalog", query="608 bearing")
        assert "bearing('SingleRowDeepGrooveBallBearing', 'M8-22-7', 'SKT')" in catalog and "outer (" in catalog
        assert "lid: closes the box" in await call("explore_design", path=path, focus="lid")
        faces = await call("list_geometry", path=path, target="lid")
        assert faces.startswith("Revision 2\ninstance lid:") and "face 0:" in faces
        gap = await call("measure_geometry", path=path, a="lid.screws[0]", b="insert[0]")
        assert "connector lid.screws[0]" in gap and "minimum distance" in gap
        assert "Revision 1 → 2" in await call("compare_revisions", path=path, base=1)
        missing = await client.call_tool("build_design", {"path": str(tmp_path / "nope")})
        assert missing.is_error and "no design.json in" in missing.content[0].text
        assert "Materials" in await call("list_backends") and "bom" in await call("list_backends")


def test_projects_get_the_skills_generated_from_the_framework(core: HitlCad, tmp_path: Path) -> None:
    written = init_project(tmp_path / "project", core.settings, "python", core.registry.all(), hooks=False, codex=False)
    assert ".claude/skills/hitlcad/" in written and ".agents/skills/hitlcad-build123d/" in written
    skill = (tmp_path / "project" / ".claude/skills/hitlcad/SKILL.md").read_text(encoding="utf-8")
    for generated in (
        '"kind": "gear"',
        '"type": "cylindrical"',
        '"type": "gear"',
        '"kind": "clearance"',
        '"process": "sheet_metal"',
        '"fastener": "lid_screw.seat"',
    ):
        assert generated in skill
    assert "$" not in skill.replace("$(", "")
    guide = (tmp_path / "project" / ".claude/skills/hitlcad-build123d/SKILL.md").read_text(encoding="utf-8")
    assert "def build(p, i)" in guide and "frames(" in guide


def test_the_prompt_hooks_reach_the_agents_without_their_shell_in_the_way(
    core: HitlCad, tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    python = str(tmp_path / "Program Files" / "python")  # a path with a space, as on Windows
    server = core.settings.base_url
    project = tmp_path / "project"
    old = {"type": "command", "command": "python -m hitlcad.cli context --hook codex --server http://x", "timeout": 5}
    agents.json_file(agents.codex_hooks_file(), lambda c: agents.prompt_hook(c, old))
    written = init_project(project, core.settings, python, core.registry.all(), hooks=True, codex=True)
    assert agents.status(project) == agents.AgentSetup(hooks=True, codex=True)

    def handlers(file: Path) -> list[dict[str, Any]]:
        config = json.loads(file.read_text(encoding="utf-8"))
        return [h for g in config.get("hooks", {}).get("UserPromptSubmit", []) for h in g["hooks"]]

    # The hooks name this machine's interpreter: only this machine reads them. The project's committed files
    # name the server alone.
    assert {agents.CLAUDE_HOOKS, ".gitignore"} <= set(written)
    assert agents.CLAUDE_HOOKS in (project / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not handlers(project / agents.CLAUDE_SETTINGS) and python not in (project / ".mcp.json").read_text()
    # Claude Code starts the hook from its argv, with no shell in between.
    claude = agents.hook_argv(python, "claude", server)
    assert handlers(project / agents.CLAUDE_HOOKS) == [
        {"type": "command", "command": python, "args": claude[1:], "timeout": agents.HOOK_TIMEOUT_S}
    ]
    # Codex hands a command line to its shell (cmd.exe /C on Windows, sh elsewhere): once that shell has read it,
    # it is the hook's argv again. The old hook is brought up to date, not doubled.
    codex = agents.hook_argv(python, "codex", server)
    [handler] = handlers(agents.codex_hooks_file())
    assert handler["command"] == agents.command_line(codex)
    echo = agents.command_line([sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", *codex])
    shell = f'cmd.exe /C "{echo}"' if sys.platform == "win32" else ["/bin/sh", "-c", echo]
    assert json.loads(subprocess.run(shell, capture_output=True, encoding="utf-8", check=True).stdout) == codex
    assert f'url = "{core.settings.mcp_url}"' in agents.codex_config_file().read_text(encoding="utf-8")


def test_the_watcher_ignores_outputs_inside_a_design_wherever_the_design_is(tmp_path: Path) -> None:
    design = tmp_path / "build" / ".git" / "gearbox"  # a checkout under folders a design itself ignores
    folders = [design]
    assert _relevant(folders, design / "parts" / "gear.py") and _relevant(folders, design / "design.json")
    for output in ("build/parts/gear.step", "parts/__pycache__/gear.pyc", ".git/index", "parts/gear.py~"):
        assert not _relevant(folders, design / output)
    assert not _relevant(folders, tmp_path / "elsewhere.py")
