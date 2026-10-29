"""The services the panel and agents use, on real builds: designs, builds, picks and pins across rebuilds, the
design graph, revision comparisons, the MCP tools and project setup."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from typing import Any

import psutil
import pytest
from mcp.client import Client

from hitlcad import hook
from hitlcad.api.mcp import build_mcp
from hitlcad.app import HitlCad
from hitlcad.backends.workers import Superseded
from hitlcad.core.events import PinsChanged, SelectionChanged
from hitlcad.core.report import Earlier
from hitlcad.core.types import ComponentRef, Pick, Pin, SelectionItem
from hitlcad.kernel.errors import HitlcadError
from hitlcad.render.base import NullRenderer
from hitlcad.review.selection import topology
from hitlcad.services.watcher import _relevant
from hitlcad.store.sqlite import SqliteStore
from hitlcad.workspace import agents
from hitlcad.workspace.document import Disk
from hitlcad.workspace.project import init_project

from .conftest import copy_design, edit_json, write_design


def face_index(shape, test) -> int:
    return next(k for k, f in enumerate(shape.faces()) if test(f))


@pytest.mark.geometry
async def test_review_measures_selected_faces_and_points_on_the_named_revision(core: HitlCad, tmp_path: Path) -> None:
    state, _ = core.designs.open(copy_design("tiny", tmp_path / "tiny"))
    rev = await core.builds.build(state.id)
    box = (await core.revisions.built(state.id, rev)).parts["box"].features.exact()
    top = face_index(box, lambda f: f.normal_at().Z > 0.99)
    bottom = face_index(box, lambda f: f.normal_at().Z < -0.99)
    picks = tuple(
        Pick(point=(0, 0, 0), component=ComponentRef(instance="box", topo="face", index=k)) for k in (top, bottom)
    )
    text = await core.feedback.measure(state.id, rev.id, picks)
    assert "minimum distance 5.000 mm" in text
    assert "gap between their planes is 5.000 mm" in text
    points = (Pick(point=(0, 0, 0)), Pick(point=(3, 4, 0)))
    assert "minimum distance 5.000 mm" in await core.feedback.measure(state.id, rev.id, points)
    await core.builds.edit_parameters(state.id, {"width": 20}, core.designs.get(state.id).version)
    sides = tuple(
        Pick(point=(0, 0, 0), component=ComponentRef(instance="box", topo="face", index=k))
        for k in (face_index(box, lambda f: f.normal_at().X > 0.99), face_index(box, lambda f: f.normal_at().X < -0.99))
    )
    assert "minimum distance 10.000 mm" in await core.feedback.measure(state.id, rev.id, sides)


async def test_a_new_design_is_scaffolded_and_waits_for_parts(core: HitlCad, tmp_path: Path) -> None:
    state, created = core.designs.open(tmp_path / "new", "build123d", "New")
    assert created == ["design.json", "interfaces.py", "assembly.py", "parts/.gitkeep"]
    assert json.loads((tmp_path / "new" / "design.json").read_text(encoding="utf-8"))["hitlcad"] == 2
    with pytest.raises(HitlcadError, match="declares no parts yet") as empty:
        await core.builds.build(state.id)
    assert empty.value.owner == "request"
    assert await core.builds.rebuild(state.id) is None


@pytest.mark.geometry
async def test_picks_name_instances_and_connectors_and_pins_follow_rebuilds(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    state, _ = core.designs.open(folder)
    rev = await core.builds.build(state.id)
    built = await core.revisions.built(state.id, rev)
    lid = built.parts["lid"].features.exact()
    top = face_index(lid, lambda f: f.is_planar and f.normal_at().Z > 0.99)
    hole = face_index(lid, lambda f: f.geom_type.name == "CYLINDER")
    (item,) = await core.feedback.describe_picks(state.id, None, [Pick(point=(0, 0, 24), component=ComponentRef(
        instance="lid", topo="face", index=hole))])  # fmt: skip
    assert item.connector and item.connector.startswith("lid.screws[") and item.label.startswith(item.connector)
    pin = await core.feedback.add_pin(state.id, Pick(point=(0, 0, 24), component=ComponentRef(
        instance="lid", topo="face", index=top)), "make it thicker")  # fmt: skip
    assert pin.instance == "lid" and "face" in pin.label
    rebuilt = await core.builds.edit_parameters(state.id, {"lid_t": 6}, core.designs.get(state.id).version)
    (carried,) = core.designs.get(state.id).open_pins
    assert carried.revision == rebuilt.id and carried.point[2] == pytest.approx(26)
    new_top = topology((await core.revisions.built(state.id, rebuilt)).parts["lid"].features, carried.component)
    assert new_top.is_planar and new_top.normal_at().Z > 0.99
    saved = json.loads((folder / "design.json").read_text(encoding="utf-8"))
    assert next(p["value"] for p in saved["parameters"] if p["name"] == "lid_t") == 6


@pytest.mark.geometry
async def test_a_traced_motion_the_human_watched_passes_until_it_changes(core: HitlCad, tmp_path: Path) -> None:
    state, _ = core.designs.open(copy_design("linkage", tmp_path / "linkage"))
    rev = await core.builds.build(state.id)

    def crank(revision: Any) -> Any:
        return next(r for r in revision.report.results if r.analysis == "motion" and r.subject == "base.a+crank.a")

    assert crank(rev).code == "motion.sampled"
    (loop,) = rev.report.motions
    watched = await core.builds.watch_motion(state.id, rev.id, loop.key)
    assert watched.origin == "review" and watched.summary == "Watched base.a ↔ crank.a"
    assert (crank(watched).code, crank(watched).status) == ("motion.watched", "pass")
    assert crank(watched).detail.endswith(f"watched in the panel on revision {rev.id}")
    assert [w.key for w in core.designs.get(state.id).watched] == [loop.key]
    longer = await core.builds.edit_parameters(
        state.id, {"coupler_l": 41}, core.designs.get(state.id).version
    )  # another loop: watched no more
    assert crank(longer).code == "motion.sampled"
    with pytest.raises(HitlcadError, match="has no traced motion"):
        await core.builds.watch_motion(state.id, longer.id, loop.key)
    again = await core.builds.watch_motion(state.id, rev.id, loop.key, watched=False)
    assert again.summary == "Unwatched base.a ↔ crank.a" and core.designs.get(state.id).watched == []


@pytest.mark.geometry
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
    await core.designs.take_in(state.id)  # as the watcher does
    assert "stale: changed since rev 1: interfaces.py" in core.explore.graph(state.id).explore()
    (folder / "design.json").write_text("{", encoding="utf-8")  # broken mid-edit: the graph says so, not rev 1
    await core.designs.take_in(state.id)
    assert "✗ design.json does not load, so this is rev 1's:" in core.explore.graph(state.id).explore()
    assert graph.structure()["nodes"][0] == {
        "id": "base",
        "kind": "made",
        "label": "base",
        "role": "open box",
        "count": 1,
        "status": "pass",
    }


async def test_the_overview_bounds_lists_and_review_keeps_all_feedback(core: HitlCad, tmp_path: Path) -> None:
    folder = copy_design("tiny", tmp_path / "tiny")
    names = [f"box_{letter}" for letter in "abcdefghijkl"]
    edit_json(folder / "design.json", lambda d: d["parts"].update({n: d["parts"]["box"] for n in names}))
    state, _ = core.designs.open(folder)
    core.designs.emit(state.id, PinsChanged(pins=[Pin(point=(0, 0, 0), note=n, label=n) for n in names]))
    core.designs.emit(state.id, SelectionChanged(items=[SelectionItem(point=(0, 0, 0), label="selected box")]))
    async with Client(build_mcp(core)) as client:
        overview = (await client.call_tool("explore_design", {"path": str(folder)})).content[0].text
        assert "… 8 more" in overview and "… 8 more review items" in overview
        assert "box_l" not in overview and "focus='review'" in overview
        detail = (await client.call_tool("explore_design", {"path": str(folder), "focus": "box_l"})).content[0].text
        assert "box_l: a box" in detail
        review = (await client.call_tool("explore_design", {"path": str(folder), "focus": "review"})).content[0].text
        assert all(n in review for n in names) and "selected box" in review


@pytest.mark.geometry
@pytest.mark.cold_cache  # says which parts were rebuilt
async def test_revisions_compare_and_designs_using_a_design_rebuild(core: HitlCad, tmp_path: Path) -> None:
    copy_design("hinge", tmp_path / "hinge")
    copy_design("shelf", tmp_path / "shelf")
    hinge, _ = core.designs.open(tmp_path / "hinge")
    shelf, _ = core.designs.open(tmp_path / "shelf")
    assert core.designs.where_used(hinge.id) == [shelf.id]
    first = await core.builds.build(hinge.id)
    second = await core.builds.edit_parameters(hinge.id, {"stop_h": 0}, core.designs.get(hinge.id).version)
    text = core.explore.compare(hinge.id, first.id, second.id)
    assert "stop_h: 30.0 → 0.0" in text and "motion base.hinge+lid.pin: fail → pass" in text
    assert "Parts rebuilt in 2: base, lid" in text

    built = await core.builds.build(shelf.id)  # its spec made again holds the hinge's files as they were
    core.designs._revisions.clear()
    again = core.designs.revision(shelf.id, built.id)
    assert again.built is not None and {at.design for at in again.built.files.values()} == {shelf.id, hinge.id}
    assert again.spec is not None and built.spec is not None and again.spec.key == built.spec.key


@pytest.mark.geometry
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

        structure = await call("build_design", path=path, scope="structure")
        assert "every reference resolves" in structure and "placed: base, lid, insert ×4, screw ×4" in structure
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
        assert missing.is_error and ": [not_found.design] no design.json in" in missing.content[0].text
        assert "\n→ Pass the absolute path of a folder holding design.json" in missing.content[0].text
        assert "Materials" in await call("list_backends") and "bom" in await call("list_backends")


@pytest.mark.geometry
async def test_build_summaries_count_omitted_problems_and_explore_returns_details(
    core: HitlCad, tmp_path: Path
) -> None:
    folder = copy_design("tiny", tmp_path / "tiny")
    requirements = [f"Review {letter}" for letter in "abcdefgh"]
    edit_json(folder / "design.json", lambda d: d.update(requirements=[{"text": text} for text in requirements]))
    async with Client(build_mcp(core)) as client:
        built = await client.call_tool("build_design", {"path": str(folder)})
        assert not built.is_error and all(c.type == "text" for c in built.content)
        text = built.content[0].text
        assert "… 3 more problems" in text and "8 unverified" in text and "Review h" not in text
        detail = await client.call_tool("explore_design", {"path": str(folder), "focus": "rev 1"})
        assert not detail.is_error and all(text in detail.content[0].text for text in requirements)


def test_projects_get_the_skills_generated_from_the_framework(core: HitlCad, tmp_path: Path) -> None:
    written = init_project(tmp_path / "project", core.settings, "python", core.registry.all(), hooks=False, codex=False)
    assert ".claude/skills/hitlcad/" in written and ".agents/skills/hitlcad-build123d/" in written
    skill = (tmp_path / "project" / ".claude/skills/hitlcad/SKILL.md").read_text(encoding="utf-8")
    reference = (tmp_path / "project" / ".claude/skills/hitlcad/reference/design-json.md").read_text(encoding="utf-8")
    assert "[reference/design-json.md](reference/design-json.md)" in skill
    for generated in (
        '"kind": "gear"',
        '"type": "cylindrical"',
        '"type": "gear"',
        '"kind": "clearance"',
        '"process": "sheet_metal"',
        '"fastener": "lid_screw.seat"',
    ):
        assert generated in reference and generated not in skill
    assert "$" not in skill.replace("$(", "") and "$" not in reference
    playbook = (tmp_path / "project" / ".claude/skills/hitlcad/playbook.md").read_text(encoding="utf-8")
    assert "- `mate.fit`: A mate's fit is not what it asks for. Size the hole and the shaft" in playbook
    assert (
        "- `manufacturing.fdm_overhangs`:" in playbook
        and "- `requirement.size`: The extent along one axis, mm." in playbook
    )
    guide = (tmp_path / "project" / ".claude/skills/hitlcad-build123d/SKILL.md").read_text(encoding="utf-8")
    assert "def build(p, i)" in guide and "frames(" in guide


def test_the_hooks_reach_the_agents_without_their_shell_in_the_way(
    core: HitlCad, tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    python = str(tmp_path / "Program Files" / "python")  # a path with a space, as on Windows
    project = tmp_path / "project"
    old = {"type": "command", "command": "python -m hitlcad.cli context --hook codex --server http://x", "timeout": 5}
    agents.json_file(agents.codex_hooks_file(), lambda c: c.setdefault("hooks", {}).setdefault(
        "UserPromptSubmit", []).append({"hooks": [old]}) or True)  # fmt: skip
    written = init_project(project, core.settings, python, core.registry.all(), hooks=True, codex=True)
    setup = agents.status(project, python)
    assert setup.hooks and setup.codex and setup.codex_config == str(tmp_path / "codex" / "config.toml")

    def groups(file: Path) -> dict[str, list[dict[str, Any]]]:
        return json.loads(file.read_text(encoding="utf-8")).get("hooks", {})

    # The hooks name this machine's interpreter, and no server (they find it as it runs): only this machine reads
    # them, and every project's are the same. The project's committed files name the server alone.
    assert {agents.CLAUDE_HOOKS, ".gitignore"} <= set(written)
    assert agents.CLAUDE_HOOKS in (project / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not groups(project / agents.CLAUDE_SETTINGS) and python not in (project / ".mcp.json").read_text()
    # Claude Code starts the hook from its argv, with no shell in between, around prompts and the tools that write
    # files, whether they succeed or fail.
    claude = agents.hook_argv(python, "claude")
    handler = {"type": "command", "command": python, "args": claude[1:], "timeout": agents.HOOK_TIMEOUT_S}
    tools = {"hooks": [handler], "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash"}
    assert groups(project / agents.CLAUDE_HOOKS) == {
        "PreToolUse": [tools],
        "PostToolUse": [tools],
        "PostToolUseFailure": [tools],
        "UserPromptSubmit": [{"hooks": [handler]}],
        "Stop": [{"hooks": [handler]}],
    }
    # Codex hands a command line to its shell (cmd.exe /C on Windows, sh elsewhere): once that shell has read it,
    # it is the hook's argv again. The old prompt hook is replaced, not doubled; setting up again changes nothing.
    codex = agents.hook_argv(python, "codex")
    hooked = groups(agents.codex_hooks_file())
    assert [g["hooks"][0]["command"] for g in hooked["UserPromptSubmit"]] == [agents.command_line(codex)]
    assert hooked["PostToolUse"][0]["matcher"] == "Bash|apply_patch"
    assert not agents.json_file(
        agents.codex_hooks_file(), lambda c: agents.hooks(c, "codex", hooked["PreToolUse"][0]["hooks"][0])
    )
    echo = agents.command_line([sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", *codex])
    shell = f'cmd.exe /C "{echo}"' if sys.platform == "win32" else ["/bin/sh", "-c", echo]
    assert json.loads(subprocess.run(shell, capture_output=True, encoding="utf-8", check=True).stdout) == codex
    assert f'url = "{core.settings.mcp_url}"' in agents.codex_config_file().read_text(encoding="utf-8")
    # A project set up by another hitlcad (its hook as that one ran it) is asked about again.
    assert not agents.status(project, sys.executable).hooks


def test_the_hook_starts_on_the_standard_library_alone_and_never_stops_the_agent(tmp_path: Path) -> None:
    """It runs around every tool call: nothing of hitlcad's to import. A server not running, one that does not
    answer, or a hook run as another hitlcad wrote it adds nothing and exits 0: an agent takes some exit codes as a
    refusal of the prompt."""
    loaded = subprocess.run([sys.executable, "-c", "import sys, hitlcad.hook; print(sorted(m for m in sys.modules "
                             "if m.startswith('hitlcad')))"], capture_output=True, encoding="utf-8", check=True)  # fmt: skip
    assert loaded.stdout.strip() == "['hitlcad', 'hitlcad.hook']"
    env = os.environ | {"HITLCAD_DATA_DIR": str(tmp_path)}

    def run(*args: str) -> tuple[int, str]:
        done = subprocess.run([sys.executable, "-m", "hitlcad.hook", *args], input='{"hook_event_name": "PostToolUse"}',
                              capture_output=True, encoding="utf-8", env=env)  # fmt: skip
        return done.returncode, done.stdout

    assert run("claude") == (0, "")  # no server
    (tmp_path / hook.SERVER_FILE).write_text('{"url": "http://127.0.0.1:9"}', encoding="utf-8")
    assert run("claude") == (0, "")  # a server that does not answer
    assert run("claude", "--server", "http://127.0.0.1:9") == (0, "")
    said = json.loads(hook.output("PostToolUse", "the human set width"))
    assert said == {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "the human set width"}}
    assert hook.output("UserPromptSubmit", "pointing at") == "pointing at"


async def test_an_agent_s_command_merges_with_what_the_human_changed_while_it_ran_and_it_hears_of_it(
    core: HitlCad, tmp_path: Path
) -> None:
    """The agent's shell command starts; the human sets the width in the panel; the command then renames the design
    in the file. The folder stays as the agent left it until its turn ends, so it need not read the file again: the
    design's files have the human's width and the agent's name at once, the agent is told the line the human
    changed with the command's result, and the folder gets both when the turn ends. While the command runs, the
    watcher leaves the design to it; one that never ends (the human denied it) ends with the next prompt."""
    project = tmp_path / "project"
    folder = copy_design("tiny", project / "designs" / "tiny")
    state, _ = core.designs.open(folder)
    session = {"session_id": "s1", "cwd": str(project)}
    sessions = core.sessions

    async def hook(event: str, **more: Any) -> str:
        return await sessions.hook("claude", session | {"hook_event_name": event} | more)

    assert await hook("UserPromptSubmit") == ""  # first seen: nothing to catch up on
    bash = {"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "sed -i ..."}}
    await hook("PreToolUse", **bash)
    assert sessions.busy(state.id)
    await core.builds.edit_parameters(state.id, {"width": 20}, core.designs.get(state.id).version)

    def design(text: str | None) -> tuple[str, int]:
        data = json.loads(text or "")
        return data["name"], data["parameters"][0]["value"]

    text = (folder / "design.json").read_text()
    assert design(text) == ("Tiny", 10)  # the folder as the agent's command found it
    (folder / "design.json").write_text(text.replace('"name": "Tiny"', '"name": "Theirs"'), encoding="utf-8")
    told = await hook("PostToolUseFailure", **bash)  # it wrote, then failed
    assert design(core.designs.files(state.id).design) == ("Theirs", 20) and not sessions.busy(state.id)
    assert design((folder / "design.json").read_text()) == ("Theirs", 10)  # as the agent left it, until the turn ends
    assert core.designs.get(state.id).changes[-1].origin == "agent:claude:s1" and not core.designs.get(state.id).crossed
    assert "the human, in the panel, changed design.json: Parameter edit: width=20" in told
    assert 'design.json line 6: `{"name": "width", "value": 20, "min": 1, "max": 50}`' in told
    await hook("Stop")
    assert design((folder / "design.json").read_text()) == ("Theirs", 20)
    assert await hook("UserPromptSubmit") == ""  # told once

    await hook("PreToolUse", **bash | {"tool_use_id": "t2"})  # denied: no PostToolUse
    assert sessions.busy(state.id)
    await hook("UserPromptSubmit")
    assert not sessions.busy(state.id)

    patch = "*** Begin Patch\n*** Update File: designs/tiny/parts/box.py\n@@\n*** End Patch"
    assert sessions._touched({"tool_name": "apply_patch", "tool_input": {"command": patch}}, str(project)) == [state.id]
    assert sessions._touched({"tool_name": "Bash", "tool_input": {"command": "ls"}}, str(project)) == [state.id]


def test_the_watcher_ignores_outputs_inside_a_design_wherever_the_design_is(tmp_path: Path) -> None:
    design = tmp_path / "build" / ".git" / "gearbox"  # a checkout under folders a design itself ignores
    folders = [design]
    assert _relevant(folders, design / "parts" / "gear.py") and _relevant(folders, design / "design.json")
    for output in ("build/parts/gear.step", "parts/__pycache__/gear.pyc", ".git/index", "parts/gear.py~"):
        assert not _relevant(folders, design / output)
    assert not _relevant(folders, tmp_path / "elsewhere.py")


@pytest.mark.geometry
async def test_a_design_edited_while_the_server_was_down_is_read_at_start_and_builds_when_opened(
    core: HitlCad, settings: Any, tmp_path: Path
) -> None:
    """What was written while no server ran is the design's before anything reads it; but a server knows every
    design it ever opened, and building each that changed at start would build them all."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)
    await core.builds.build(state.id)
    await core.shutdown()
    spec = json.loads((folder / "design.json").read_text(encoding="utf-8"))
    spec["parameters"][0]["value"] = 12
    (folder / "design.json").write_text(json.dumps(spec), encoding="utf-8")

    later = HitlCad.create(settings, registry=core.registry, renderer=NullRenderer())
    await later.start()
    assert json.loads(later.designs.files(state.id).design or "")["parameters"][0]["value"] == 12
    await asyncio.sleep(1)  # more than the debounce
    assert len(later.designs.get(state.id).revisions) == 1
    later.designs.ensure(folder)
    for _ in range(600):
        if len(later.designs.get(state.id).revisions) == 2:
            break
        await asyncio.sleep(0.05)
    assert later.designs.get(state.id).revisions[-1].summary == "Rebuilt after the files changed"
    later.designs.ensure(folder)  # opened again, its files unchanged: not again
    await asyncio.sleep(1)
    assert len(later.designs.get(state.id).revisions) == 2
    await later.shutdown()


@pytest.mark.geometry
async def test_a_history_another_version_wrote_is_set_aside_and_a_new_one_begins(
    core: HitlCad, settings: Any, tmp_path: Path
) -> None:
    state, _ = core.designs.open(copy_design("tiny", tmp_path / "tiny"))
    await core.shutdown()
    # what a build of hitlcad whose events had another field wrote
    odd = json.dumps({"type": "design_updated", "seq": 9, "ts": "2026-01-01T00:00:00Z", "renamed": True})
    with closing(sqlite3.connect(settings.data_dir / "hitlcad.db")) as db, db:
        db.execute("INSERT INTO events VALUES (?, 9, 'design_updated', '2026-01-01', ?)", (state.id, odd))
        written = [body for (body,) in db.execute("SELECT body FROM events WHERE design = ? ORDER BY seq", (state.id,))]

    later = HitlCad.create(settings, registry=core.registry, renderer=NullRenderer())  # a newer hitlcad, same data
    reopened = later.designs.get(state.id)
    [aside] = (settings.data_dir / "designs" / state.id).glob("unreadable-*")
    assert (aside / "events.jsonl").read_text(encoding="utf-8").splitlines() == written  # kept as it was
    assert reopened.error and str(aside) in reopened.error.message and reopened.path == state.path
    assert reopened.error.code == "store.unreadable"
    revision = await later.builds.build(state.id)
    assert revision.report.status == "succeeded" and later.designs.get(state.id).error is None
    await later.shutdown()


async def test_a_design_json_that_does_not_validate_makes_a_revision_that_says_where(
    core: HitlCad, tmp_path: Path
) -> None:
    folder = write_design(tmp_path / "broken", {"design.json": {
        "hitlcad": 2, "name": "broken", "backend": "build123d",
        "parts": {"a": {"role": "a", "file": "parts/a.py", "colour": "red"}},
        "mates": [{"type": "rigid", "a": "a.top"}],
    }})  # fmt: skip
    state, _ = core.designs.open(folder)  # opened all the same: its build says what is wrong
    assert state.name == "broken"
    rev = await core.builds.build(state.id)
    assert rev.report.status == "failed" and rev.spec is None
    assert [(r.analysis, r.code, r.subject) for r in rev.report.results] == [
        ("build", "design.invalid", "parts.a.colour"),
        ("build", "design.invalid", "mates.0.rigid.b"),
    ]
    assert await core.builds.rebuild(state.id) is not None  # the first time the watcher sees these files
    assert await core.builds.rebuild(state.id) is None  # the same files, the same failure: not again
    async with Client(build_mcp(core)) as client:
        built = (await client.call_tool("build_design", {"path": str(folder)})).content[0].text
        assert "✗ build parts.a.colour (design.json) [design.invalid]: Extra inputs are not permitted" in built
        assert built.count("→ Fix each field") == 1  # the usual fix, once per code
        assert "Same as revision 2: the change since did not reach it." in built
        overview = (await client.call_tool("explore_design", {"path": str(folder)})).content[0].text
        assert "design.json does not load" in overview


async def test_a_bug_in_hitlcad_reads_as_one_and_not_as_the_agent_s_mistake(
    core: HitlCad, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = write_design(tmp_path / "d", {"design.json": {"hitlcad": 2, "name": "d", "backend": "build123d"}})

    def broken(*_: Any) -> Any:
        return [][1]  # an IndexError: a LookupError, which the agent once read as its own

    monkeypatch.setattr(core.explore, "graph", broken)
    async with Client(build_mcp(core)) as client:
        answer = await client.call_tool("explore_design", {"path": str(folder)})
    assert answer.is_error
    assert answer.content[0].text == (
        "Error executing tool explore_design: [hitlcad.bug] IndexError: list index out of range\n"
        "This is a bug in hitlcad, not in the design: do not change the design to avoid it. Tell the human."
    )


async def test_a_design_lists_its_revisions_and_reads_one_when_asked(
    core: HitlCad, settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = write_design(tmp_path / "d", {"design.json": {"hitlcad": 2, "name": "d", "backend": "build123d",
                                                           "parts": {"a": {"role": "a", "file": "parts/a.py"}}}})  # fmt: skip
    state, _ = core.designs.open(folder)
    built = await core.builds.build(state.id)  # fails: parts/a.py does not exist
    await core.shutdown()

    later = HitlCad.create(settings, registry=core.registry, renderer=NullRenderer())
    reads: list[int] = []
    read = later.designs.store.revision
    monkeypatch.setattr(later.designs.store, "revision", lambda d, r: reads.append(r) or read(d, r))
    (listed,) = later.designs.get(state.id).revisions
    assert listed.status == "failed" and listed.failure and listed.failure.code == "build.part_file_missing"
    assert reads == []  # opening a design reads no revision's spec or report
    assert later.designs.revision(state.id, 1) == built and reads == [1]
    assert later.designs.revision(state.id, 1) == built and reads == [1]  # kept, once read
    assert later.designs.earlier(state.id, 2, None) == Earlier(
        1, frozenset({("build.part_file_missing", "parts/a.py")})
    )
    with pytest.raises(HitlcadError, match="revision 7 not found"):
        later.designs.revision(state.id, 7)
    await later.shutdown()


def test_a_database_of_another_layout_is_set_aside_whole(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    store.create("dsg_1")
    store.bind("/work/fan", "dsg_1")
    store.close()
    same = SqliteStore(tmp_path)
    assert same.folders() == {"/work/fan": "dsg_1"}  # the same layout, whatever version wrote it, stays
    same.close()
    with closing(sqlite3.connect(tmp_path / "hitlcad.db")) as db, db:
        db.execute("UPDATE meta SET value = '0' WHERE key = 'schema'")
    again = SqliteStore(tmp_path)
    assert again.folders() == {} and again.set_aside_folders == {"/work/fan"}
    (aside,) = tmp_path.glob("hitlcad.*.db")
    with closing(sqlite3.connect(aside)) as db:  # kept as it was
        assert db.execute("SELECT path, design FROM folders").fetchall() == [("/work/fan", "dsg_1")]
    again.close()


@pytest.mark.geometry
async def test_a_server_keeps_each_design_s_worker_between_its_builds(settings: Any, tmp_path: Path) -> None:
    """A worker started with the server takes the first design's builds, which find build123d loaded and what it
    loaded the build before, but the design's files as they are then (lib/ as edited, not as kept); another design
    gets a worker of its own; none outlive the server."""
    core = HitlCad.create(settings, renderer=NullRenderer())
    await core.start()
    workers = core.registry.get("build123d").workers
    widths = []

    async def worker_of(design_id: str) -> int:
        revision = await core.builds.build(design_id)
        assert revision.report.status == "succeeded", revision.report.lead()
        widths.append(revision.report.metrics.size[0])  # type: ignore[union-attr]
        held = [h for h in workers._idle if h.design]
        return next(h.worker.pid for h in held if h.design == str(Path(core.designs.get(design_id).path).resolve()))

    for _ in range(200):  # warm: started before any build asks
        if workers._idle:
            break
        await asyncio.sleep(0.05)
    (warm,) = workers._idle
    folder = write_design(tmp_path / "kept", {
        "design.json": {"hitlcad": 2, "name": "Kept", "backend": "build123d", "ground": "block",
                        "parts": {"block": {"role": "a block", "file": "parts/block.py", "material": "PLA"}}},
        "lib/size.py": "WIDTH = 10\n",
        "parts/block.py": "from build123d import Box\nfrom lib.size import WIDTH\n\n\ndef build(p, i):\n"
                          "    return {'block': (Box(WIDTH, 10, 10), {})}\n",
        "assembly.py": "from build123d import Location\n\n\ndef build(p, i):\n    return {'block': [Location()]}\n",
    })  # fmt: skip
    kept, _ = core.designs.open(folder)
    assert await worker_of(kept.id) == warm.worker.pid
    (folder / "lib/size.py").write_text("WIDTH = 20\n", encoding="utf-8")
    assert await worker_of(kept.id) == warm.worker.pid and widths == [10, 20]
    other_design, _ = core.designs.open(copy_design("tiny", tmp_path / "tiny"))
    other = await worker_of(other_design.id)
    assert other != warm.worker.pid
    await core.shutdown()
    assert (
        workers._idle == [] and not psutil.pid_exists(other) or psutil.Process(other).status() == psutil.STATUS_ZOMBIE
    )


@pytest.mark.geometry
async def test_a_build_of_newer_files_supersedes_the_one_running(core: HitlCad, tmp_path: Path) -> None:
    """The running build stops once its part files are done, and records nothing; the newer one takes what they
    built from the part cache, and both callers get its revision."""
    folder = write_design(tmp_path / "pair", {
        "design.json": {"hitlcad": 2, "name": "Pair", "backend": "build123d", "ground": "slow",
                        "parameters": [{"name": "w", "value": 10, "min": 1, "max": 50}],
                        "parts": {"slow": {"role": "a block", "file": "parts/slow.py", "material": "PLA"},
                                  "fast": {"role": "a block", "file": "parts/fast.py", "material": "PLA"}}},
        "parts/slow.py": "import time\nfrom build123d import Box\n\n\ndef build(p, i):\n    time.sleep(3)\n"
                         "    return {'slow': (Box(10, 10, 10), {})}\n",
        "parts/fast.py": "from build123d import Box\n\n\ndef build(p, i):\n    return {'fast': (Box(p['w'], 10, 10), {})}\n",
        "assembly.py": "from build123d import Location\n\n\ndef build(p, i):\n"
                       "    return {'slow': [Location()], 'fast': [Location((50, 0, 0))]}\n",
    })  # fmt: skip
    state, _ = core.designs.open(folder)
    first = asyncio.create_task(core.builds.build(state.id))
    work = core.designs.store.revision_dir(state.id, 1) / "work"
    for _ in range(600):  # until its part files run
        if list(work.glob("part-*/job.json")):
            break
        await asyncio.sleep(0.05)
    edit_json(folder / "design.json", lambda d: d["parameters"][0].update(value=20))
    second = await core.builds.build(state.id)
    assert (await first).id == second.id == 1 and len(core.designs.get(state.id).revisions) == 1
    assert core.designs.get(state.id).building is None
    assert {p.name: p.cached for p in second.report.parts} == {"slow": True, "fast": False}
    assert next(p for p in second.report.parts if p.name == "fast").metrics.size[0] == 20  # type: ignore[union-attr]


@pytest.mark.geometry
async def test_a_build_superseded_by_files_that_build_nothing_ends_its_building(core: HitlCad, tmp_path: Path) -> None:
    folder = write_design(tmp_path / "slow", {
        "design.json": {"hitlcad": 2, "name": "Slow", "backend": "build123d", "ground": "slow",
                        "parts": {"slow": {"role": "a block", "file": "parts/slow.py", "material": "PLA"}}},
        "parts/slow.py": "import time\nfrom build123d import Box\n\n\ndef build(p, i):\n    time.sleep(3)\n"
                         "    return {'slow': (Box(10, 10, 10), {})}\n",
        "assembly.py": "from build123d import Location\n\n\ndef build(p, i):\n    return {'slow': [Location()]}\n",
    })  # fmt: skip
    state, _ = core.designs.open(folder)
    first = asyncio.create_task(core.builds.build(state.id))
    while core.designs.get(state.id).building is None:
        await asyncio.sleep(0.05)
    edit_json(folder / "design.json", lambda d: d.update(parts={}, ground=None))
    with pytest.raises(HitlcadError, match="declares no parts yet"):
        await core.builds.build(state.id)
    with pytest.raises(Superseded):
        await first
    assert core.designs.get(state.id).building is None and not core.designs.get(state.id).revisions


async def test_a_panel_edit_made_on_an_earlier_version_keeps_what_was_written_since(
    core: HitlCad, tmp_path: Path
) -> None:
    """The panel showed version 1; the agent renamed the design meanwhile, in its own layout of design.json: the
    human's width merges into it, every other character as the agent wrote it. Both changing the same value is
    merged too, by rule, and said: the human looks at what it made."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)
    theirs = json.loads((folder / "design.json").read_text()) | {"name": "Theirs"}
    (folder / "design.json").write_text(json.dumps(theirs, indent=4), encoding="utf-8")
    assert (await core.designs.take_in(state.id)) is not None and core.designs.get(state.id).version == 2
    await core.builds.edit_parameters(state.id, {"width": 20}, 1)
    assert (folder / "design.json").read_text() == json.dumps(theirs, indent=4).replace('"value": 10', '"value": 20')
    assert core.designs.get(state.id).changes[-1].origin == "panel" and not core.designs.get(state.id).crossed

    edit_json(folder / "design.json", lambda d: d["parameters"][0].update(value=15))  # the agent, from version 3
    await core.designs.take_in(state.id)
    await core.builds.edit_parameters(state.id, {"width": 30}, 3)
    (crossed,) = core.designs.get(state.id).crossed
    assert crossed.origin == "panel" and crossed.others == ["disk"]
    merged = json.loads((folder / "design.json").read_text())["parameters"][0]["value"]  # both kept: look at it
    assert [(c.path, c.now) for c in crossed.crossed] == [("design.json", f'"value": {merged},')]
    with pytest.raises(HitlcadError, match="versions 1 to 5") as unknown:
        await core.builds.edit_parameters(state.id, {"width": 30}, 7)
    assert unknown.value.code == "request.base_unknown"


async def test_a_revision_keeps_what_it_built_from_and_only_the_builds_something_needs_keep_their_files(
    core: HitlCad, tmp_path: Path, monkeypatch: Any
) -> None:
    """A revision keeps the version of the files it built, not a copy: its spec is made again from them, the same. A
    build's folder holds no code and no report, and its part files are the part cache's own. Once a newer build is
    made, an older one nothing points at lets go of its files, and opened again makes them again; one an open pin is
    on keeps them."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)
    first = await core.builds.build(state.id)
    assert first.spec is not None
    with closing(sqlite3.connect(core.settings.data_dir / "hitlcad.db")) as db:
        (body,) = db.execute("SELECT body FROM revisions WHERE id = ?", (first.id,)).fetchone()
    stored = json.loads(body)
    assert "spec" not in stored and stored["built"]["files"] == {
        str(folder.resolve()): {"design": state.id, "version": 1}
    }
    core.designs._revisions.clear()  # read back from the store
    again = core.designs.revision(state.id, first.id)
    assert again.spec is not None and again.spec.key == first.spec.key
    files = core.designs.store.revision_dir(state.id, first.id)
    assert not (files / "src").exists() and not (files / "report.json").exists()
    assert (files / "parts" / "box.stl").stat().st_nlink > 1  # the part cache's own file

    second = await core.builds.edit_parameters(state.id, {"width": 20}, first.version)
    assert not files.exists() and core.designs.store.revision_dir(state.id, second.id).is_dir()
    assert (
        await core.revisions.files(state.id, again) == files and (files / again.report.artifact("view").path).is_file()
    )  # type: ignore[union-attr]

    await core.feedback.add_pin(state.id, Pick(point=(0, 0, 2.5)), "this face", second.id)

    async def nowhere(design_id: str, revision: Any) -> None:  # the pin does not follow the next build
        pass

    monkeypatch.setattr(core.feedback, "carry", nowhere)
    await core.builds.edit_parameters(state.id, {"width": 30}, second.version)
    assert core.designs.store.revision_dir(state.id, second.id).is_dir() and not files.exists()


async def test_undo_takes_back_the_human_s_panel_edit_and_keeps_what_others_wrote_since(
    core: HitlCad, tmp_path: Path
) -> None:
    """The human sets the width in the panel; the agent renames the design. Undo takes the width back and keeps the
    agent's name; Redo makes it again; a new panel edit leaves nothing to redo. Undo names the step it takes back, so
    a second click is not taken for an undo of an edit the human has not seen. Where the agent changed the same value
    since, the Undo is kept and said; where it set the value back already, Undo finds nothing left and moves on."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)

    def read() -> tuple[str, int]:
        design = json.loads((folder / "design.json").read_text())
        return design["name"], design["parameters"][0]["value"]

    await core.builds.edit_parameters(state.id, {"width": 20}, 1)
    edit_json(folder / "design.json", lambda d: d.update(name="Theirs"))
    await core.designs.take_in(state.id)
    (edit,) = state.undo
    assert edit.summary == "Parameter edit: width=20" and not state.redo

    undone = await core.builds.revert(state.id, edit.version)
    assert read() == ("Theirs", 10) and not state.undo and state.redo[0].summary == edit.summary
    assert undone.origin == "undo" and undone.summary == "Undo: Parameter edit: width=20"
    with pytest.raises(HitlcadError) as gone:
        await core.builds.revert(state.id, edit.version)  # a second click on what it showed
    assert gone.value.code == "request.not_undoable"
    assert (await core.builds.revert(state.id, state.redo[-1].version)).origin == "redo"
    assert read() == ("Theirs", 20) and [e.summary for e in state.undo] == [edit.summary] and not state.redo
    await core.builds.revert(state.id, state.undo[-1].version)
    await core.builds.edit_parameters(state.id, {"width": 30}, state.version)
    assert read() == ("Theirs", 30) and len(state.undo) == 1 and not state.redo

    def width(value: int) -> Any:
        return lambda doc: doc.write_text(
            "design.json", re.sub(r'"value": \d+', f'"value": {value}', doc.text("design.json"), count=1)
        )

    await core.designs.change(state.id, state.version, "panel", width(40), "width=40")
    edit_json(folder / "design.json", lambda d: d["parameters"][0].update(value=35))  # the agent, meanwhile
    await core.designs.take_in(state.id)
    change = await core.designs.revert(state.id, state.undo[-1].version, "Undo: width=40")
    assert change is not None and change.crossings and read() == ("Theirs", 30)  # the human's Undo is kept, and said

    await core.designs.change(state.id, state.version, "panel", width(45), "width=45")
    edit_json(folder / "design.json", lambda d: d["parameters"][0].update(value=30))  # the agent set it back already
    await core.designs.take_in(state.id)
    change = await core.designs.revert(state.id, state.undo[-1].version, "Undo: width=45")
    assert (
        change is not None and change.files == [] and read() == ("Theirs", 30) and state.redo[-1].summary == "width=45"
    )


async def test_a_design_whose_folder_is_renamed_keeps_its_history(core: HitlCad, tmp_path: Path) -> None:
    """The human renames a design's folder: opened where it is now, it is the same design, its revisions with it. A
    folder that only looks like it (its files edited since) is a design of its own."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)
    await core.builds.build(state.id)
    folder.rename(tmp_path / "small")
    moved, _ = core.designs.open(tmp_path / "small")
    assert moved.id == state.id and moved.path == str((tmp_path / "small").resolve()) and len(moved.revisions) == 1
    assert list(core.designs.folders()) == [moved.path]
    copy = copy_design("tiny", tmp_path / "other")
    (tmp_path / "small").rename(tmp_path / "gone")
    edit_json(copy / "design.json", lambda d: d.update(name="Other"))
    assert core.designs.open(copy)[0].id != state.id


async def test_what_an_agent_s_turn_writes_builds_once_when_the_turn_ends(core: HitlCad, tmp_path: Path) -> None:
    """The agent moves a part to a file of its own in two writes, design.json first: the design between them names
    a file that is not there yet. Its rebuild waits for the turn, which builds the change once, whole."""
    project = tmp_path / "project"
    folder = copy_design("tiny", project / "designs" / "tiny")
    state, _ = core.designs.open(folder)
    await core.builds.build(state.id)

    async def write(n: int, rel: str, text: str) -> None:
        event = {"session_id": "s1", "cwd": str(project), "tool_name": "Write", "tool_use_id": f"w{n}",
                 "tool_input": {"file_path": str(folder / rel)}}  # fmt: skip
        await core.sessions.hook("claude", event | {"hook_event_name": "PreToolUse"})
        (folder / rel).write_text(text, encoding="utf-8")
        await core.sessions.hook("claude", event | {"hook_event_name": "PostToolUse"})

    await write(1, "design.json", (folder / "design.json").read_text().replace("parts/box.py", "parts/block.py"))
    await asyncio.sleep(1)  # more than the debounce
    assert len(state.revisions) == 1
    await write(2, "parts/block.py", (folder / "parts" / "box.py").read_text())
    await core.sessions.hook("claude", {"session_id": "s1", "cwd": str(project), "hook_event_name": "Stop"})
    for _ in range(600):
        if len(state.revisions) == 2 and not state.building:
            break
        await asyncio.sleep(0.05)
    assert [r.status for r in state.revisions] == ["succeeded", "succeeded"]


async def test_an_editor_s_save_of_a_buffer_older_than_the_file_keeps_what_was_written_since(
    core: HitlCad, tmp_path: Path
) -> None:
    """The human opened design.json in the editor; the panel set the width meanwhile, which the folder got at once;
    then the buffer, renamed, is saved over it. Told what the buffer was read from, the save merges from there: the
    width stays."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)
    buffer = (folder / "design.json").read_bytes().decode()  # as the editor reads it: its line endings kept
    await core.builds.edit_parameters(state.id, {"width": 20}, 1)
    core.designs.saving(folder / "design.json", sha256(buffer.encode()).hexdigest())
    (folder / "design.json").write_bytes(buffer.replace('"name": "Tiny"', '"name": "Theirs"').encode())
    await core.designs.take_in(state.id)
    saved = json.loads((folder / "design.json").read_text())
    assert (saved["name"], saved["parameters"][0]["value"]) == ("Theirs", 20) and not state.crossed


async def test_a_folder_read_while_an_edit_merged_does_not_undo_it(
    core: HitlCad, tmp_path: Path, monkeypatch: Any
) -> None:
    """The watcher reads the folder, off the event loop, as the human sets the width: the edit waits for the read to
    be taken in, so the folder the read saw is not taken for a write that undid it."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)
    reading, go, read = asyncio.Event(), threading.Event(), Disk.read
    loop = asyncio.get_running_loop()

    def slow(path: Path) -> Disk:
        disk = read(path)
        loop.call_soon_threadsafe(reading.set)
        go.wait(5)
        return disk

    monkeypatch.setattr(Disk, "read", staticmethod(slow))
    taking_in = asyncio.create_task(core.designs.take_in(state.id))
    await reading.wait()
    monkeypatch.setattr(Disk, "read", staticmethod(read))
    edit = asyncio.create_task(core.designs.change(state.id, 1, "panel", lambda doc: doc.write_text(
        "design.json", doc.text("design.json").replace('"value": 10', '"value": 20'))))  # fmt: skip
    go.set()
    await asyncio.gather(taking_in, edit)
    assert json.loads(core.designs.files(state.id).design)["parameters"][0]["value"] == 20
    assert core.designs.files(state.id).design == (folder / "design.json").read_bytes().decode()
    assert [c.origin for c in core.designs.get(state.id).changes] == ["folder", "panel"]


@pytest.mark.geometry
async def test_the_outputs_the_human_chooses_are_written_by_the_next_build(core: HitlCad, tmp_path: Path) -> None:
    """The panel asks for a STEP of the assembly as an agent would: in design.json `outputs`, then a build, whose
    parts come from the cache."""
    folder = copy_design("tiny", tmp_path / "tiny")
    state, _ = core.designs.open(folder)
    first = await core.builds.build(state.id)
    assert first.report.artifact("step") is None  # review needs no STEP of the whole
    revision = await core.builds.edit_outputs(state.id, ["step"], first.version)
    assert revision.origin == "outputs_edit" and json.loads((folder / "design.json").read_text())["outputs"] == ["step"]
    assert revision.report.artifact("step") is not None and (folder / "build" / "model.step").is_file()
    assert all(part.cached for part in revision.report.parts)
    with pytest.raises(HitlcadError, match="no output 'dwg'") as unknown:
        await core.builds.edit_outputs(state.id, ["dwg"], revision.version)
    assert unknown.value.owner == "request"


def test_a_design_s_document_replays_every_version_from_the_store(tmp_path: Path, monkeypatch: Any) -> None:
    """Versions after a snapshot replay from it; a server started again reads the same files."""
    from hitlcad.services import documents

    monkeypatch.setattr(documents, "SNAPSHOT_EVERY", 2)
    folder = copy_design("tiny", tmp_path / "tiny")
    store = SqliteStore(tmp_path / "data")
    store.create("d")
    kept = documents.DocumentService(store)
    kept.create("d", folder, Disk.read(folder), "folder")
    for width in (11, 12, 13, 14):
        kept.change("d", kept.current("d").version, "panel", lambda doc, w=width: doc.write_text(
            "assembly.py", (doc.text("assembly.py") or "") + f"# {w}\n"))  # fmt: skip
    kept.write("d", folder)
    again = documents.DocumentService(store)
    assert again.current("d") == kept.current("d") and again.current("d").version == 5
    written = (folder / "assembly.py").read_bytes().decode()  # its line endings as they are
    assert again.files_at("d", 3).texts["assembly.py"] == written.replace("# 13\n# 14\n", "")
    assert written.endswith("# 14\n")  # the folder written with the latest version
    store.close()


def test_one_server_keeps_a_data_folder(tmp_path: Path) -> None:
    """Each server keeps its designs' state in memory: a second one on the same folder would part from it."""
    first = SqliteStore(tmp_path)
    with pytest.raises(HitlcadError, match="another hitlcad server keeps") as taken:
        SqliteStore(tmp_path)
    assert (taken.value.code, taken.value.owner) == ("store.in_use", "environment")
    first.close()
    SqliteStore(tmp_path).close()  # free once the first has closed
