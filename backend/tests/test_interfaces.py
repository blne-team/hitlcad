"""The REST API and event streams the VS Code panel and extension use, the file viewer, and the CLI hook."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import threading
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import trimesh

from hitlcad import cli
from hitlcad.api.rest import _until_gone, create_app
from hitlcad.app import HitlCad
from hitlcad.config import Settings
from hitlcad.core.events import SelectionChanged
from hitlcad.geometry import cadview
from hitlcad.render.base import NullRenderer
from hitlcad.services.bus import MAX_QUEUE, EventBus

from .conftest import copy_design


@pytest.fixture
async def api(core: HitlCad) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(core)), base_url="http://127.0.0.1"
    ) as client:
        yield client


@pytest.mark.geometry
async def test_rest_design_lifecycle(api: httpx.AsyncClient, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    missing = await api.post("/api/designs", json={"path": str(tmp_path / "missing")})
    assert missing.status_code == 404 and missing.json()["diagnostic"]["code"] == "not_found.design"
    opened = (await api.post("/api/designs", json={"path": str(folder)})).json()
    design_id = opened["id"]
    assert opened["name"] == "Enclosure" and opened["created"] is False

    built = await api.post(f"/api/designs/{design_id}/build")
    assert built.status_code == 201 and built.json()["report"]["status"] == "succeeded"
    step = await api.get(f"/api/designs/{design_id}/revisions/1/files/parts/lid.step")
    assert step.status_code == 200 and step.content.startswith(b"ISO-10303-21")
    assert (await api.get(f"/api/designs/{design_id}/revisions/1/files/../../etc/passwd")).status_code == 404
    scene = (await api.get(f"/api/designs/{design_id}/revisions/1/files/scene.json")).json()
    assert scene["shapes"]["parts"][0]["id"] == "/Enclosure/base"
    part_view = await api.get(f"/api/designs/{design_id}/revisions/1/files/{scene['instances'][0]['path']}")
    assert part_view.status_code == 200 and cadview.PartView.header(part_view.content)["templates"]
    assert (await api.post(f"/api/designs/{design_id}/build", json={"configuration": "nope"})).status_code == 422

    structure = (await api.get(f"/api/designs/{design_id}/structure")).json()
    assert {n["id"] for n in structure["graph"]["nodes"]} == {"base", "lid", "insert", "screw"}
    assert any(e["kind"] == "fastener" for e in structure["graph"]["edges"]) and "10 instances" in structure["overview"]

    too_wide = await api.post(f"/api/designs/{design_id}/parameters", json={"values": {"width": 9999}, "base": 1})
    assert too_wide.status_code == 422 and too_wide.json()["diagnostic"]["code"] == "parameter.invalid"
    pin = (await api.post(f"/api/designs/{design_id}/pins", json={
        "point": [0, 0, 24], "note": "here", "component": {"instance": "lid", "topo": "solid"}})).json()  # fmt: skip
    assert pin["instance"] == "lid" and pin["label"].startswith("Whole part")
    patched = await api.patch(f"/api/designs/{design_id}/pins/{pin['id']}", json={"resolved": True})
    assert patched.json()["resolved"] is True and patched.json()["note"] == "here"
    assert (await api.delete(f"/api/designs/{design_id}/pins/{pin['id']}")).status_code == 204
    selected = await api.put(f"/api/designs/{design_id}/selection", json={
        "items": [{"point": [0, 0, 24], "component": {"instance": "screw[2]", "topo": "solid"}}]})  # fmt: skip
    assert selected.json()["items"][0]["instance"] == "screw[2]"


@pytest.mark.geometry
async def test_a_build_s_parts_are_seen_before_they_are_checked(
    core: HitlCad, api: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once its parts are placed, the build running serves its scene and part views (nothing else of it) while its
    analyses run, and says how far it has got: live, never kept in the log, and gone with its revision."""
    state, _ = core.designs.open(copy_design("tiny", tmp_path / "tiny"))
    checking, go_on = threading.Event(), threading.Event()
    first = core.builds.pipeline.analyses[0]
    run = first.run

    def held(ctx: Any) -> Any:
        checking.set()
        go_on.wait(60)
        return run(ctx)

    monkeypatch.setattr(first, "run", held)
    queue = core.designs.bus.subscribe(state.id)
    build = asyncio.create_task(core.builds.build(state.id))
    try:
        await asyncio.to_thread(checking.wait, 60)
        progress = core.designs.get(state.id).building
        assert progress is not None and progress.revision == 1 and progress.scene == cadview.SCENE_FILE
        assert progress.doing == f"checking {first.name} (1 of {len(core.builds.pipeline.analyses)})"
        assert progress.results == []  # nothing checked yet
        files = f"/api/designs/{state.id}/revisions/1/files"
        scene = (await api.get(f"{files}/{progress.scene}")).json()
        part_view = await api.get(f"{files}/{scene['instances'][0]['path']}")
        assert part_view.status_code == 200 and cadview.PartView.header(part_view.content)["templates"]
        assert (await api.get(f"{files}/parts/box.step")).status_code == 404
    finally:
        go_on.set()
    revision = await build
    live = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None and event.type == "build_progress":
            live.append(event)
    shown = next(n for n, e in enumerate(live) if e.scene)
    assert [e.doing for e in live[: shown + 1]] == ["building its parts"] * (shown + 1)  # seen before anything checked
    assert all(e.seq == 0 and e.revision == revision.id for e in live)
    # each analysis's results came as it finished, grouped as the report groups them
    found = [len(e.results) for e in live]
    assert found == sorted(found) and live[-1].results == revision.report.results
    assert live[-1].problems == revision.report.problems and live[-1].tally == revision.report.tally
    analyses = [a.name for a in core.builds.pipeline.analyses]
    assert list(revision.report.durations) == ["plan", "components", "part files", "scene", "measuring connectors",
                                               *analyses, "report", "outputs"]  # fmt: skip
    assert not {"build_progress", "build_stopped"} & {e.type for e in core.designs.store.events(state.id)}
    assert core.designs.get(state.id).building is None
    assert (await api.get(f"/api/designs/{state.id}/revisions/1/files/parts/box.step")).status_code == 200


@pytest.mark.geometry
async def test_model_files_for_the_file_viewer(api: httpx.AsyncClient, tmp_path: Path) -> None:
    stl = tmp_path / "cube.stl"
    trimesh.creation.box((10, 20, 30)).export(stl)
    scene = await api.get("/api/files/cad", params={"path": str(stl)}, follow_redirects=True)
    assert scene.status_code == 200 and scene.json()["shapes"]["bb"]["zmax"] == pytest.approx(15)
    (instance,) = scene.json()["instances"]
    part_view = await api.get(str(scene.url.join(instance["path"])))
    assert part_view.status_code == 200 and cadview.PartView.header(part_view.content)["bounds"][1][2] == pytest.approx(
        15
    )
    assert (await api.get(str(scene.url.join("../nope/scene.json")))).status_code == 404
    picked = await api.post("/api/files/describe", json={"path": str(stl), "point": [5, 0, 0]})
    assert picked.json()["label"].startswith("Point on cube at")
    assert (await api.get("/api/files/cad", params={"path": str(tmp_path / "a.txt")})).status_code == 404


def test_panel_webviews_may_call_the_api_and_open_its_streams_and_other_pages_may_not(
    core: HitlCad, tmp_path: Path
) -> None:
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    state, _ = core.designs.open(copy_design("tiny", tmp_path / "tiny"))
    client = TestClient(create_app(core), base_url="http://127.0.0.1")
    ok = client.get("/api/health", headers={"Origin": "vscode-webview://abc123"})
    assert ok.headers["access-control-allow-origin"] == "vscode-webview://abc123"
    other = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert other.status_code == 403 and "access-control-allow-origin" not in other.headers
    for path in (f"ws://127.0.0.1/api/designs/{state.id}/stream?after=0", "ws://127.0.0.1/api/activity"):
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect(path, headers={"Origin": "https://evil.example"}),
        ):
            pass
        assert refused.value.code == 1008
    with client.websocket_connect(
        f"ws://127.0.0.1/api/designs/{state.id}/stream", headers={"Origin": "vscode-webview://abc"}
    ) as ws:
        assert json.loads(ws.receive_text())["type"] == "design_opened"


def test_only_requests_addressed_to_this_machine_or_the_url_agents_are_given_reach_the_api(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    settings = Settings(data_dir=tmp_path / "data", backends=["build123d"], public_url="http://devbox.local:8000")
    app = create_app(HitlCad.create(settings, renderer=NullRenderer()))
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        mcp = client.post("http://devbox.local:8000/mcp", json=initialize,
                          headers={"Accept": "application/json, text/event-stream"})  # fmt: skip
        assert mcp.status_code == 200 and "hitlcad" in mcp.text
        for here in ("localhost:8000", "[::1]:8000"):
            assert client.get("/api/health", headers={"Host": here}).status_code == 200
        assert client.get("/api/health", headers={"Host": "elsewhere.local:8000"}).status_code == 400
        rebound = client.post("/api/projects", headers={"Host": "rebound.example:8000"},
                              json={"path": str(tmp_path / "project")})  # fmt: skip
        assert rebound.status_code == 400 and not (tmp_path / "project").exists()
        assert client.post("/mcp", headers={"Host": "rebound.example:8000"}, json={}).status_code == 400


@pytest.mark.geometry
def test_streams_replay_follow_and_report_builds(core: HitlCad, tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    state, _ = core.designs.open(copy_design("tiny", tmp_path / "tiny"))
    client = TestClient(create_app(core), base_url="http://127.0.0.1")
    with client.websocket_connect(f"ws://127.0.0.1/api/designs/{state.id}/stream") as ws:
        assert json.loads(ws.receive_text())["type"] == "design_opened"  # the history so far
        assert json.loads(ws.receive_text())["type"] == "document_changed"  # its files, version 1
        client.put(f"/api/designs/{state.id}/selection", json={"items": []})
        assert json.loads(ws.receive_text())["type"] == "selection_changed"
    with client.websocket_connect("ws://127.0.0.1/api/activity") as ws:
        assert client.post(f"/api/designs/{state.id}/build").status_code == 201
        build = json.loads(ws.receive_text())
    assert (build["name"], build["revision"], build["status"]) == ("Tiny", 1, "succeeded")


async def test_a_stream_that_falls_behind_is_closed_so_its_client_resyncs() -> None:
    bus = EventBus()
    queue = bus.subscribe("d")
    for _ in range(MAX_QUEUE + 1):
        bus.publish("d", SelectionChanged(items=[]))

    class Socket:
        closed: int | None = None

        async def receive(self) -> None:
            await asyncio.Event().wait()  # the client stays connected

        async def close(self, code: int, reason: str) -> None:
            self.closed = code

    socket = Socket()
    sent = await asyncio.wait_for(_collect(_until_gone(socket, queue)), timeout=5)  # type: ignore[arg-type]
    assert sent == [] and socket.closed == 1013
    bus.publish("d", SelectionChanged(items=[]))
    assert queue.empty()


async def _collect(items: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in items]


def test_the_context_hook_never_fails_the_prompt(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": "/x", "session_id": "s"})))

    def down(*_: Any, **__: Any) -> Any:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert cli.main(["context", "--hook", "claude", "--server", "http://127.0.0.1:9"]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.geometry
def test_the_cli_writes_utf8_whatever_its_pipe_would_take(tmp_path: Path) -> None:
    # PYTHONIOENCODING=ascii stands in, on every platform, for a pipe on Windows: its code page has no —.
    folder = copy_design("tiny", tmp_path / "tiny")
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "HITLCAD_DATA_DIR": str(tmp_path / "data")}
    run = subprocess.run([sys.executable, "-m", "hitlcad.cli", "build", str(folder), "--out", str(tmp_path / "out"),
                          "--no-render"], capture_output=True, env=env)  # fmt: skip
    assert run.stderr == b""
    assert "Revision 0 — SUCCEEDED" in run.stdout.decode("utf-8")
