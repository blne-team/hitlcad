"""The REST API and event streams the VS Code panel and extension use, the file viewer, and the CLI hook."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import trimesh

from hitlcad import cli
from hitlcad.api.app import _until_gone, create_app
from hitlcad.app import HitlCad
from hitlcad.config import Settings
from hitlcad.core.events import SelectionChanged
from hitlcad.render.base import NullRenderer
from hitlcad.service.bus import MAX_QUEUE, EventBus

from .conftest import copy_design

pytestmark = pytest.mark.geometry


@pytest.fixture
async def api(core: HitlCad) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(core)), base_url="http://127.0.0.1"
    ) as client:
        yield client


async def test_rest_design_lifecycle(api: httpx.AsyncClient, tmp_path: Path) -> None:
    folder = copy_design("enclosure", tmp_path / "enclosure")
    assert (await api.post("/api/designs", json={"path": str(tmp_path / "missing")})).status_code == 404
    opened = (await api.post("/api/designs", json={"path": str(folder)})).json()
    design_id = opened["id"]
    assert opened["name"] == "Enclosure" and opened["created"] is False

    built = await api.post(f"/api/designs/{design_id}/build")
    assert built.status_code == 201 and built.json()["report"]["status"] == "succeeded"
    step = await api.get(f"/api/designs/{design_id}/revisions/1/files/parts/lid.step")
    assert step.status_code == 200 and step.content.startswith(b"ISO-10303-21")
    assert (await api.get(f"/api/designs/{design_id}/revisions/1/files/../../etc/passwd")).status_code == 404
    view = (await api.get(f"/api/designs/{design_id}/revisions/1/files/view.json")).json()
    assert view["shapes"]["parts"][0]["id"] == "/Enclosure/base"

    wide = await api.post(f"/api/designs/{design_id}/build", json={"configuration": "wide"})
    assert wide.json()["spec"]["configuration"] == "wide" and wide.json()["report"]["values"]["width"] == 60
    assert (await api.post(f"/api/designs/{design_id}/build", json={"configuration": "nope"})).status_code == 422

    structure = (await api.get(f"/api/designs/{design_id}/structure")).json()
    assert {n["id"] for n in structure["graph"]["nodes"]} == {"base", "lid", "insert", "screw"}
    assert any(e["kind"] == "fastener" for e in structure["graph"]["edges"]) and "10 instances" in structure["overview"]

    assert (await api.post(f"/api/designs/{design_id}/parameters", json={"values": {"width": 9999}})).status_code == 422
    pin = (await api.post(f"/api/designs/{design_id}/pins", json={
        "point": [0, 0, 24], "note": "here", "component": {"instance": "lid", "topo": "solid"}})).json()  # fmt: skip
    assert pin["instance"] == "lid" and pin["label"].startswith("Whole part")
    patched = await api.patch(f"/api/designs/{design_id}/pins/{pin['id']}", json={"resolved": True})
    assert patched.json()["resolved"] is True and patched.json()["note"] == "here"
    assert (await api.delete(f"/api/designs/{design_id}/pins/{pin['id']}")).status_code == 204
    selected = await api.put(f"/api/designs/{design_id}/selection", json={
        "items": [{"point": [0, 0, 24], "component": {"instance": "screw[2]", "topo": "solid"}}]})  # fmt: skip
    assert selected.json()["items"][0]["instance"] == "screw[2]"


async def test_model_files_for_the_file_viewer(api: httpx.AsyncClient, tmp_path: Path) -> None:
    stl = tmp_path / "cube.stl"
    trimesh.creation.box((10, 20, 30)).export(stl)
    view = await api.get("/api/files/cad", params={"path": str(stl)})
    assert view.status_code == 200 and view.json()["shapes"]["bb"]["zmax"] == pytest.approx(15)
    picked = await api.post("/api/files/describe", json={"path": str(stl), "point": [5, 0, 0]})
    assert picked.json()["label"].startswith("Point on cube at")
    assert (await api.get("/api/files/cad", params={"path": str(tmp_path / "a.txt")})).status_code == 404


async def test_panel_webviews_may_call_the_api_and_other_pages_may_not(api: httpx.AsyncClient) -> None:
    ok = await api.get("/api/health", headers={"Origin": "vscode-webview://abc123"})
    assert ok.headers["access-control-allow-origin"] == "vscode-webview://abc123"
    other = await api.get("/api/health", headers={"Origin": "https://evil.example"})
    assert other.status_code == 403 and "access-control-allow-origin" not in other.headers


def test_other_pages_open_no_event_streams(core: HitlCad, tmp_path: Path) -> None:
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    state, _ = core.designs.open(copy_design("enclosure", tmp_path / "enclosure"))
    client = TestClient(create_app(core), base_url="http://127.0.0.1")
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


def test_the_url_agents_are_given_reaches_every_route(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    settings = Settings(data_dir=tmp_path / "data", backends=["build123d"], public_url="http://devbox.local:8000")
    app = create_app(HitlCad.create(settings, renderer=NullRenderer()))
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        mcp = client.post("http://devbox.local:8000/mcp", json=initialize,
                          headers={"Accept": "application/json, text/event-stream"})  # fmt: skip
        assert mcp.status_code == 200 and "hitlcad" in mcp.text
        assert client.get("/api/health", headers={"Host": "[::1]:8000"}).status_code == 200
        assert client.get("/api/health", headers={"Host": "elsewhere.local:8000"}).status_code == 400


async def test_only_requests_addressed_to_this_machine_reach_the_api(api: httpx.AsyncClient, tmp_path: Path) -> None:
    assert (await api.get("/api/health", headers={"Host": "localhost:8000"})).status_code == 200
    rebound = await api.post(
        "/api/projects", headers={"Host": "rebound.example:8000"}, json={"path": str(tmp_path / "project")}
    )
    assert rebound.status_code == 400 and not (tmp_path / "project").exists()
    assert (await api.post("/mcp", headers={"Host": "rebound.example:8000"}, json={})).status_code == 400


def test_streams_replay_follow_and_report_builds(core: HitlCad, tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    folder = copy_design("enclosure", tmp_path / "enclosure")
    state, _ = core.designs.open(folder)
    client = TestClient(create_app(core), base_url="http://127.0.0.1")
    with client.websocket_connect(f"ws://127.0.0.1/api/designs/{state.id}/stream") as ws:
        assert [json.loads(ws.receive_text())["type"] for _ in range(2)] == ["design_opened", "design_updated"]
        client.put(f"/api/designs/{state.id}/selection", json={"items": []})
        assert json.loads(ws.receive_text())["type"] == "selection_changed"
    with client.websocket_connect("ws://127.0.0.1/api/activity") as ws:
        assert client.post(f"/api/designs/{state.id}/build").status_code == 201
        build = json.loads(ws.receive_text())
    assert (build["name"], build["revision"], build["status"]) == ("Enclosure", 1, "succeeded")


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


def test_the_cli_writes_utf8_whatever_its_pipe_would_take(tmp_path: Path) -> None:
    # PYTHONIOENCODING=ascii stands in, on every platform, for a pipe on Windows: its code page has no —.
    folder = copy_design("shelf", tmp_path / "shelf")
    copy_design("hinge", tmp_path / "hinge")  # a component of the shelf
    run = subprocess.run([sys.executable, "-m", "hitlcad.cli", "build", str(folder), "--out", str(tmp_path / "out"),
                          "--no-render"], capture_output=True, env={**os.environ, "PYTHONIOENCODING": "ascii"})  # fmt: skip
    assert run.stderr == b""
    assert "Revision 0 — SUCCEEDED" in run.stdout.decode("utf-8")
