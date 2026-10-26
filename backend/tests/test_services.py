"""The services the panel and agents use, on real builds: designs, builds, picks and pins across rebuilds, the
design graph, revision comparisons, the MCP tools and project setup."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from hitlcad.api.mcp import build_mcp
from hitlcad.app import HitlCad
from hitlcad.core.report import Earlier
from hitlcad.core.types import ComponentRef, Pick
from hitlcad.kernel.errors import HitlcadError
from hitlcad.render.base import NullRenderer
from hitlcad.review.selection import topology
from hitlcad.services.watcher import _relevant
from hitlcad.store.sqlite import SqliteStore
from hitlcad.workspace import agents
from hitlcad.workspace.project import init_project

from .conftest import copy_design, write_design


def face_index(shape, test) -> int:
    return next(k for k, f in enumerate(shape.faces()) if test(f))


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


@pytest.mark.geometry
async def test_a_loop_the_human_watched_passes_until_it_changes(core: HitlCad, tmp_path: Path) -> None:
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
    longer = await core.builds.edit_parameters(state.id, {"coupler_l": 41})  # another loop: watched no more
    assert crank(longer).code == "motion.sampled"
    with pytest.raises(HitlcadError, match="has no loop motion"):
        await core.builds.watch_motion(state.id, longer.id, loop.key)
    again = await core.builds.watch_motion(state.id, rev.id, loop.key, watched=False)
    assert again.summary == "Unwatched base.a ↔ crank.a" and core.designs.get(state.id).watched == []


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


@pytest.mark.geometry
@pytest.mark.cold_cache  # says which parts were rebuilt
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
        assert missing.is_error and ": [not_found.design] no design.json in" in missing.content[0].text
        assert "\n→ Pass the absolute path of a folder holding design.json" in missing.content[0].text
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
    playbook = (tmp_path / "project" / ".claude/skills/hitlcad/playbook.md").read_text(encoding="utf-8")
    assert "- `mate.fit`: A mate's fit is not what it asks for. Size the hole and the shaft" in playbook
    assert (
        "- `manufacturing.fdm_overhangs`:" in playbook
        and "- `requirement.size`: The extent along one axis, mm." in playbook
    )
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
    setup = agents.status(project)
    assert setup.hooks and setup.codex and setup.codex_config == str(tmp_path / "codex" / "config.toml")

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
async def test_a_server_keeps_a_build_process_started_for_each_backend(settings: Any, tmp_path: Path) -> None:
    """So a save builds without waiting for build123d (or Blender) to load."""
    core = HitlCad.create(settings.model_copy(update={"warm_builds": 1}), renderer=NullRenderer())
    core.start()
    backend = core.registry.get("build123d")
    kept = backend.executor._ready  # type: ignore[attr-defined]
    for _ in range(200):
        if kept.get(tuple(backend.command())):  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0.05)
    (spare,) = kept[tuple(backend.command())]  # type: ignore[attr-defined]
    state, _ = core.designs.open(copy_design("tiny", tmp_path / "tiny"))
    revision = await core.builds.build(state.id)
    assert revision.report.status == "succeeded", revision.report.lead()
    assert spare.proc.returncode is not None  # it took the build's first job, and ended with it
    await core.shutdown()
    assert kept == {}


def test_one_server_keeps_a_data_folder(tmp_path: Path) -> None:
    """Each server keeps its designs' state in memory: a second one on the same folder would part from it."""
    first = SqliteStore(tmp_path)
    with pytest.raises(HitlcadError, match="another hitlcad server keeps") as taken:
        SqliteStore(tmp_path)
    assert (taken.value.code, taken.value.owner) == ("store.in_use", "environment")
    first.close()
    SqliteStore(tmp_path).close()  # free once the first has closed
