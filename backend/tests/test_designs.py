"""Design folders, pins and context for agents, project scaffolding, and the MCP tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from hitlcad import describe, skills
from hitlcad.api.mcp_server import build_mcp
from hitlcad.core.events import ErrorRaised
from hitlcad.core.types import Pick, RevisionOrigin
from hitlcad.designs import agents
from hitlcad.designs.folder import DesignFile, DesignFolder
from hitlcad.designs.project import init_project
from hitlcad.service.designs import NotFoundError

from .conftest import BOX_SOURCE, box_params, brief, make_core


async def eventually(predicate: Any, timeout: float = 8) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not reached")
        await asyncio.sleep(0.05)


def fake_design(path: Path, width: float = 20) -> DesignFolder:
    folder = DesignFolder(path)
    path.mkdir(parents=True)
    (path / "model.py").write_text(BOX_SOURCE)
    folder.save(DesignFile(name="Box", backend="fake", parameters=box_params(width=width)))
    return folder


@pytest.fixture
async def core(settings: Any, fake_backend: Any) -> Any:
    core = make_core(settings, fake_backend)
    core.start()
    yield core
    await core.shutdown()


async def opened(core: Any, folder: DesignFolder) -> str:
    """Open the folder and wait for its first build; returns the design id."""
    design_id = core.designs.open(str(folder.path))[0].id
    await eventually(lambda: len(core.designs.get(design_id).revisions) == 1)
    return design_id


async def test_open_builds_and_binds_one_design(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)
    state = core.designs.get(design_id)
    assert state.path == str(folder.path) and state.name == "Box" and state.backend == "fake"
    rev = state.revisions[0]
    assert rev.origin == RevisionOrigin.FILE and rev.report.metrics.size[0] == pytest.approx(20)
    assert core.designs.open(str(folder.path))[0].id == design_id  # same folder, same design
    assert [s.path for s in core.designs.list()] == [str(folder.path)]
    with pytest.raises(NotFoundError):
        core.designs.open(str(tmp_path / "missing"))

    new, created = core.designs.open(str(tmp_path / "new"), "fake", "New")
    assert created == ["design.json"] and not (tmp_path / "new" / "model.py").exists()  # the agent writes the model
    assert await core.builds.rebuild(new.id) is None  # nothing to build yet
    with pytest.raises(ValueError, match="model.py does not exist yet"):
        await core.builds.build(new.id)
    with pytest.raises(ValueError, match="unknown backend 'nope'; available: fake"):
        core.designs.open(str(tmp_path / "other"), "nope", "Other")
    with pytest.raises(ValueError, match="absolute"):
        core.designs.open("relative/path")


async def test_saving_files_rebuilds_and_unchanged_saves_are_skipped(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)

    design = folder.load()
    design.parameters[0] = design.parameters[0].model_copy(update={"value": 35.0})
    folder.save(design)  # as if an agent edited design.json
    await eventually(lambda: len(core.designs.get(design_id).revisions) == 2)
    assert core.designs.get(design_id).revisions[-1].report.metrics.size[0] == pytest.approx(35)

    (folder.path / "model.py").write_text(BOX_SOURCE)  # same content: no new revision
    (folder.path / "notes.txt").write_text("irrelevant")  # unrelated file: ignored
    await asyncio.sleep(1.2)
    assert len(core.designs.get(design_id).revisions) == 2


async def test_brief_edits_reach_the_panel_without_rebuilding(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)
    folder.save(folder.load().model_copy(update={"brief": brief()}))
    await eventually(lambda: core.designs.get(design_id).brief is not None)
    assert core.designs.get(design_id).brief.title == "Test box"
    await asyncio.sleep(1.0)
    assert len(core.designs.get(design_id).revisions) == 1  # the brief does not affect geometry


async def test_successful_builds_are_exported_into_the_folder(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)
    build = folder.path / "build"
    assert {p.name for p in build.iterdir()} == {"mesh.stl", "build.log", "report.json", ".gitignore"}  # not view.json
    assert (build / ".gitignore").read_text().endswith("*\n")  # never committed

    (folder.path / "model.py").write_text(BOX_SOURCE + "# FAIL\n")
    failed = await core.builds.build(design_id)
    assert failed.report.status == "failed" and (build / "mesh.stl").is_file()  # last good outputs stay
    await asyncio.sleep(1.0)
    assert len(core.designs.get(design_id).revisions) == 2  # writing build/ does not trigger rebuilds

    mine = fake_design(tmp_path / "own")
    (mine.path / "build").mkdir()
    (mine.path / "build" / "notes.txt").write_text("not hitlcad's")
    mine_id = await opened(core, mine)
    assert [p.name for p in (mine.path / "build").iterdir()] == ["notes.txt"]  # someone else's folder: untouched
    error = core.designs.get(mine_id).error  # and the panel says why build/ was not written
    assert error and error.startswith("revision 1 built, but ") and "not created by hitlcad" in error


async def test_parameter_edits_are_written_back(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)

    rev = await core.builds.edit_parameters(design_id, {"height": 9})
    assert rev.origin == RevisionOrigin.PARAMETER_EDIT and rev.parent == 1 and "height=9" in rev.summary
    assert {p.name: p.value for p in folder.load().parameters}["height"] == 9
    await asyncio.sleep(1.2)
    assert len(core.designs.get(design_id).revisions) == 2  # the write-back did not trigger another build
    with pytest.raises(ValueError):
        await core.builds.edit_parameters(design_id, {"height": 9999})


async def test_invalid_design_file_is_reported(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)
    folder.design_file.write_text('{"hitlcad": 1, "name": "Box", "backend": "fake", "parameters": [{"name": "Bad"')
    with pytest.raises(ValueError, match="cannot build"):
        await core.builds.build(design_id)
    assert any(isinstance(e, ErrorRaised) and "invalid" in e.message for e in core.designs.store.events(design_id))
    assert "invalid" in core.designs.get(design_id).error


async def test_pins_and_context_for_agents(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "proj" / "designs" / "box")
    design_id = await opened(core, folder)
    svc, context = core.feedback, core.context.read
    assert context(tmp_path / "proj") == ""

    pin = await svc.add_pin(design_id, Pick(point=(10, 5, 5)), "make this wall thicker")
    other = await svc.add_pin(design_id, Pick(point=(0, 0, 5)))
    assert pin.revision == 1 and pin.label  # described against the mesh
    assert svc.update_pin(design_id, other.id, note="round this corner").note == "round this corner"

    text = context(tmp_path / "proj")
    assert "Design 'Box'" in text and "make this wall thicker" in text and pin.id in text
    assert "round this corner" in text
    assert context(tmp_path / "elsewhere") == ""  # only designs under the root

    # The prompt hook: in full when changed for that session, a reminder while unchanged.
    deliver = core.context.deliver
    first = deliver(tmp_path / "proj", "claude", "s1")
    assert "make this wall thicker" in first and pin.id in first
    delivered = core.designs.get(design_id).deliveries["s1"]
    assert delivered.agent == "claude" and set(delivered.pins) == {pin.id, other.id}
    again = deliver(tmp_path / "proj", "claude", "s1")
    assert "make this wall thicker" not in again and "unchanged" in again and pin.id in again
    assert "make this wall thicker" in deliver(tmp_path / "proj", "codex", "s2")  # another session gets it all
    assert deliver(tmp_path / "elsewhere", "claude", "s3") == ""

    resolved = svc.resolve_pins(design_id, [pin.id], "wall is now 3 mm")
    assert resolved[0].resolved and resolved[0].reply == "wall is now 3 mm"
    after = deliver(tmp_path / "proj", "claude", "s1")
    assert "make this wall thicker" not in after and "round this corner" in after
    svc.remove_pin(design_id, other.id)
    assert core.designs.get(design_id).open_pins == []
    assert "Nothing is selected or pinned any more" in deliver(tmp_path / "proj", "claude", "s1")
    assert deliver(tmp_path / "proj", "claude", "s1") == ""  # said once
    with pytest.raises(NotFoundError):
        svc.resolve_pins(design_id, ["pin_nope"])

    items = await svc.set_selection(design_id, [Pick(point=(10, 0, 2.5), normal=(1, 0, 0))])
    assert items[0].revision == 1 and "Selected in the 3D view" in context(tmp_path / "proj")

    # A rebuild moves the selection and open pins onto the new build where their spot is found.
    open_pin = await svc.add_pin(design_id, Pick(point=(10, 0, 2.5)), "here")
    widened = await core.builds.edit_parameters(design_id, {"width": 22})  # the +X side moves from x=10 to x=11
    state = core.designs.get(design_id)
    carried = next(p for p in state.pins if p.id == open_pin.id)
    assert carried.revision == widened.id and carried.point == pytest.approx((11, 0, 2.5))
    assert state.selection[0].revision == widened.id and state.selection[0].id == items[0].id
    assert "not found" not in describe.feedback_text(state)


async def test_registry_survives_restart(settings: Any, fake_backend: Any, tmp_path: Path) -> None:
    first = make_core(settings, fake_backend)
    folder = fake_design(tmp_path / "box")
    design_id = first.designs.open(str(folder.path))[0].id
    second = make_core(settings, fake_backend)
    assert second.designs.id_for(folder.path) == design_id


def test_init_project_is_idempotent_and_merges_configs(
    tmp_path: Path, fake_backend: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    (root / ".claude" / "settings.json").write_text(json.dumps({"model": "opus", "hooks": {"Stop": []}}))
    (root / "CLAUDE.md").write_text("mine")
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text(json.dumps({"editor.tabSize": 2}))
    url = "http://127.0.0.1:8000"

    def init(python: str = "/opt/tools/hitlcad/bin/python", hooks: bool = True, codex: bool = False) -> list[str]:
        return init_project(root, url, python, [fake_backend], hooks=hooks, codex=codex)

    written = init()
    assert set(written) == {
        "designs/",
        ".mcp.json",
        ".claude/settings.json",
        ".codex/hooks.json",
        ".vscode/settings.json",
        "AGENTS.md",
        ".gitignore",
        *(f"{base}/{name}/" for base in skills.SKILL_DIRS for name in ("hitlcad", "hitlcad-fake")),
    }
    vscode = json.loads((root / ".vscode" / "settings.json").read_text())
    assert vscode == {"editor.tabSize": 2, "python.defaultInterpreterPath": "/opt/tools/hitlcad/bin/python"}
    servers = json.loads((root / ".mcp.json").read_text())["mcpServers"]
    assert servers["other"] == {"command": "x"} and servers["hitlcad"]["url"].endswith("/mcp")
    claude = json.loads((root / ".claude" / "settings.json").read_text())
    assert claude["model"] == "opus" and "Stop" in claude["hooks"] and claude["enabledMcpjsonServers"] == ["hitlcad"]
    hook = "/opt/tools/hitlcad/bin/python -m hitlcad.cli context --hook {} --server http://127.0.0.1:8000"
    assert claude["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == hook.format("claude")
    codex = json.loads((root / ".codex" / "hooks.json").read_text())
    assert codex["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == hook.format("codex")
    assert (root / "CLAUDE.md").read_text() == "mine"  # never overwritten
    for base in skills.SKILL_DIRS:
        assert "name: hitlcad\n" in (root / base / "hitlcad" / "SKILL.md").read_text()
        assert "Define build(p)." in (root / base / "hitlcad-fake" / "SKILL.md").read_text()
    assert init() == []
    assert init("/new/python") == [
        ".claude/settings.json",
        ".codex/hooks.json",
        ".vscode/settings.json",
    ]  # kept current: the hooks run hitlcad's interpreter
    assert len(json.loads((root / ".codex" / "hooks.json").read_text())["hooks"]["UserPromptSubmit"]) == 1
    (root / ".claude/skills/hitlcad/SKILL.md").write_text("edited")
    (root / ".agents/skills/hitlcad-gone").mkdir()
    assert init("/new/python") == [
        ".claude/skills/hitlcad/",
        ".agents/skills/hitlcad-gone/",
    ]  # hitlcad's skills are restored and pruned
    assert not (root / ".agents/skills/hitlcad-gone").exists()
    (root / ".vscode" / "settings.json").write_text('{\n  // mine\n  "a": 1\n}')
    with pytest.raises(ValueError, match="not plain JSON"):
        init("/new/python")


def test_agent_hooks_and_codex_config_only_with_permission(
    tmp_path: Path, fake_backend: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_file = tmp_path / "codex" / "config.toml"
    monkeypatch.setenv("CODEX_HOME", str(codex_file.parent))
    codex_file.parent.mkdir()
    codex_file.write_text('model = "gpt"  # mine\n\n[projects."/elsewhere"]\ntrust_level = "trusted"\n')
    root = (tmp_path / "proj").resolve()

    def init(hooks: bool, codex: bool) -> list[str]:
        return init_project(root, "http://h:1", "/py", [fake_backend], hooks=hooks, codex=codex)

    assert ".codex/hooks.json" not in init(hooks=False, codex=False)
    claude = json.loads((root / ".claude" / "settings.json").read_text())
    assert claude == {"enabledMcpjsonServers": ["hitlcad"]}  # the MCP server, but no hook
    assert agents.status(root) == agents.AgentSetup(hooks=False, codex=False)
    assert codex_file.read_text().count("trust_level") == 1  # untouched

    assert set(init(hooks=True, codex=True)) == {".claude/settings.json", ".codex/hooks.json", str(codex_file)}
    assert agents.status(root) == agents.AgentSetup(hooks=True, codex=True)
    text = codex_file.read_text()
    assert text.startswith('model = "gpt"  # mine\n\n[projects."/elsewhere"]\ntrust_level = "trusted"\n')  # kept
    assert f'[projects."{root}"]\ntrust_level = "trusted"' in text
    assert '[mcp_servers.hitlcad]\nurl = "http://h:1/mcp"' in text
    assert init(hooks=True, codex=True) == []


async def test_mcp_tools(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    async with Client(build_mcp(core)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"list_backends", "create_design", "get_design", "build_design",
                         "get_feedback", "resolve_pins", "get_revision", "list_geometry", "measure_geometry"}  # fmt: skip

        built = await client.call_tool("build_design", {"path": str(folder.path), "summary": "check"})
        assert "SUCCEEDED" in built.content[0].text and "X 20.00 × Y 10.00" in built.content[0].text
        no_brep = await client.call_tool(
            "list_geometry", {"path": str(folder.path)}
        )  # the fake backend builds meshes only
        assert (
            no_brep.content[0].text.startswith("ERROR: revision")
            and "only build123d designs" in no_brep.content[0].text
        )
        design_id = core.designs.id_for(folder.path)
        assert (
            "Nothing is selected"
            in (await client.call_tool("get_feedback", {"path": str(folder.path)})).content[0].text
        )

        pin = await core.feedback.add_pin(design_id, Pick(point=(10, 5, 5)), "thicker")
        feedback = (await client.call_tool("get_feedback", {"path": str(folder.path)})).content[0].text
        assert pin.id in feedback and "thicker" in feedback
        rebuilt = await client.call_tool("build_design", {"path": str(folder.path)})
        assert "1 open pin(s)" in rebuilt.content[0].text
        done = await client.call_tool("resolve_pins", {"path": str(folder.path), "pin_ids": [pin.id], "reply": "ok"})
        assert "0 still open" in done.content[0].text

        state = (await client.call_tool("get_design", {"path": str(folder.path)})).content[0].text
        assert "Design 'Box' (fake)" in state and "Revision 2" in state
        old = (await client.call_tool("get_revision", {"path": str(folder.path), "revision": 1})).content[0].text
        assert "check" in old and BOX_SOURCE.strip() in old
        missing = await client.call_tool("build_design", {"path": str(tmp_path / "nope")})
        assert missing.content[0].text.startswith("ERROR")


async def test_edits_made_while_stopped_are_built_on_start(settings: Any, fake_backend: Any, tmp_path: Path) -> None:
    first = make_core(settings, fake_backend)
    first.start()
    folder = fake_design(tmp_path / "box")
    design_id = await opened(first, folder)
    await first.shutdown()

    unchanged = make_core(settings, fake_backend)
    unchanged.start()
    await asyncio.sleep(0.8)
    assert len(unchanged.designs.get(design_id).revisions) == 1  # nothing changed: no rebuild on start
    await unchanged.shutdown()

    design = folder.load()
    design.parameters[0] = design.parameters[0].model_copy(update={"value": 44.0})
    folder.save(design)  # edited while hitlcad was down
    restarted = make_core(settings, fake_backend)
    restarted.start()
    await eventually(lambda: len(restarted.designs.get(design_id).revisions) == 2)
    assert restarted.designs.get(design_id).revisions[-1].report.metrics.size[0] == pytest.approx(44)
    await restarted.shutdown()


async def test_declared_fdm_process_is_checked_and_part_of_the_build(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)
    assert not any(c.name.startswith("fdm_") for c in core.designs.get(design_id).current_revision.report.checks)

    data = json.loads(folder.design_file.read_text())
    data["manufacturing"] = {"process": "fdm", "min_wall_mm": 1.2}
    folder.design_file.write_text(json.dumps(data))
    rebuilt = await core.builds.rebuild(design_id)  # the process is part of what a build depends on
    assert rebuilt is not None and rebuilt.spec.manufacturing and rebuilt.spec.manufacturing.min_wall_mm == 1.2
    names = {c.name for c in rebuilt.report.checks}
    assert {"fdm_wall_thickness", "fdm_overhangs", "fdm_bed_contact"} <= names
    assert await core.builds.rebuild(design_id) is None  # unchanged: skipped


async def test_a_crashing_check_fails_the_build_visibly(core: Any, tmp_path: Path, monkeypatch: Any) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)

    def broken(_: Any) -> list[Any]:
        raise RuntimeError("check bug")

    monkeypatch.setattr("hitlcad.analysis.mesh.CHECKS", [broken])
    with pytest.raises(RuntimeError, match="check bug"):
        await core.builds.build(design_id)
    state = core.designs.get(design_id)
    assert not state.building and state.error == "the build crashed: check bug"


async def test_requirements_are_verified_and_part_of_the_build(core: Any, tmp_path: Path) -> None:
    folder = fake_design(tmp_path / "box")
    design_id = await opened(core, folder)
    assert core.designs.get(design_id).current_revision.report.requirements == []

    data = json.loads(folder.design_file.read_text())
    data["requirements"] = [
        {"text": "20 mm wide", "check": {"kind": "size", "axis": "x", "min": 19.9, "max": 20.1}},
        {"text": "a 3 mm hole", "check": {"kind": "hole", "diameter": 3}},
        {"text": "nice edges"},
    ]
    folder.design_file.write_text(json.dumps(data))
    rebuilt = await core.builds.rebuild(design_id)  # requirements are part of what a build depends on
    assert rebuilt is not None and [r.text for r in rebuilt.spec.requirements] == [
        "20 mm wide",
        "a 3 mm hole",
        "nice edges",
    ]
    assert [r.status for r in rebuilt.report.requirements] == ["pass", "unverified", "unverified"]
    assert "B-rep" in rebuilt.report.requirements[1].detail  # the fake backend builds meshes only
    text = describe.revision_text(rebuilt)
    assert "Requirements: 1 pass, 0 fail, 2 unverified" in text and "✓ 20 mm wide: X 20.00 mm" in text
