"""REST API used by the VS Code panel and extension, and the CLI hook."""

from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path
from typing import Any

import httpx
import pytest
import trimesh

from hitlcad import cli
from hitlcad.api.app import create_app

from .conftest import make_core
from .test_designs import fake_design


@pytest.fixture
async def api(settings: Any, fake_backend: Any) -> Any:
    core = make_core(settings, fake_backend)
    app = create_app(core)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, core


async def test_rest_design_lifecycle(api: Any, tmp_path: Path) -> None:
    client, core = api
    folder = fake_design(tmp_path / "box")
    assert (await client.post("/api/designs", json={"path": str(tmp_path / "missing")})).status_code == 404
    r = await client.post("/api/designs", json={"path": str(folder.path)})
    assert r.status_code == 200 and r.json()["name"] == "Box" and r.json()["created"] is False
    design_id = r.json()["id"]

    built = await client.post(f"/api/designs/{design_id}/build")
    assert built.status_code == 201 and built.json()["report"]["status"] == "succeeded"
    view = await client.get(f"/api/designs/{design_id}/revisions/1/files/view.json")
    assert view.status_code == 200 and view.headers["content-type"] == "application/json"
    assert view.json()["shapes"]["bb"]["zmax"] == pytest.approx(5)
    assert (await client.get(f"/api/designs/{design_id}/revisions/1/files/job.json")).status_code == 404

    edit = await client.post(f"/api/designs/{design_id}/parameters", json={"values": {"width": 40}})
    assert edit.status_code == 201 and edit.json()["origin"] == "parameter_edit"
    assert (
        await client.post(f"/api/designs/{design_id}/parameters", json={"values": {"width": 9999}})
    ).status_code == 422

    pin = (await client.post(f"/api/designs/{design_id}/pins", json={"point": [1, 2, 5], "note": "here"})).json()
    assert pin["note"] == "here" and pin["resolved"] is False
    patched = await client.patch(f"/api/designs/{design_id}/pins/{pin['id']}", json={"resolved": True})
    assert patched.json()["resolved"] is True and patched.json()["note"] == "here"
    assert (await client.patch(f"/api/designs/{design_id}/pins/pin_nope", json={"note": "x"})).status_code == 404
    assert (await client.delete(f"/api/designs/{design_id}/pins/{pin['id']}")).status_code == 204

    assert (await client.get(f"/api/designs/{design_id}")).json()["selection"] == []
    sel = await client.put(f"/api/designs/{design_id}/selection", json={"items": [{"point": [20, 0, 2.5]}]})
    assert sel.status_code == 200 and sel.json()["items"][0]["revision"] == 2
    context = await client.get("/api/context", params={"root": str(tmp_path)})
    assert context.headers["content-type"].startswith("text/plain") and "Selected in the 3D view" in context.text
    body = {"root": str(tmp_path), "agent": "codex", "session": "c1"}
    delivered = await client.post("/api/context/deliveries", json=body)
    assert "Selected in the 3D view" in delivered.text
    assert "Selected in the 3D view" not in (await client.post("/api/context/deliveries", json=body)).text

    state = (await client.get(f"/api/designs/{design_id}")).json()
    assert len(state["revisions"]) == 2 and state["pins"] == [] and state["selection"]
    listing = (await client.get("/api/designs")).json()
    assert listing[0]["id"] == design_id and listing[0]["revisions"] == 2
    events = (await client.get(f"/api/designs/{design_id}/events", params={"after": 2})).json()
    assert events[0]["seq"] == 3 and {e["type"] for e in events} >= {"pins_changed", "revision_created"}
    assert (await client.get("/api/designs/dsg_missing")).status_code == 404
    assert (await client.put("/api/designs/dsg_missing/selection", json={"items": []})).status_code == 404
    assert (await client.post("/api/designs/dsg_missing/build")).status_code == 404
    assert (await client.post("/api/designs/dsg_missing/pins", json={"point": [0, 0, 0]})).status_code == 404
    assert (await client.get("/api/designs/..%2Fevil")).status_code == 404
    caps = (await client.get("/api/capabilities")).json()
    assert caps["backends"][0]["name"] == "fake" and caps["mcp_url"].endswith("/mcp")


async def test_model_files_for_the_3d_viewer(api: Any, tmp_path: Path) -> None:
    client, _ = api
    stl = tmp_path / "box.stl"
    trimesh.creation.box((40, 30, 10)).export(stl)

    view = await client.get("/api/files/cad", params={"path": str(stl)})
    assert view.status_code == 200 and view.headers["content-type"] == "application/json"
    (part,) = view.json()["shapes"]["parts"]
    assert part["id"].startswith("/box/") and view.json()["shapes"]["bb"]["xmax"] == pytest.approx(20)
    (box,) = view.json()["instances"]
    assert box["segments_per_edge"]["shape"] == [1]  # the 12 box edges form one connected run of creases

    pick = {"path": str(stl), "point": [20, 0, 0]}
    described = await client.post("/api/files/describe", json=pick)
    assert (
        described.status_code == 200 and described.json()["kind"] == "point" and described.json()["component"] is None
    )
    with_component = {**pick, "component": {"part": part["id"], "topo": "face", "index": 0}}
    assert (await client.post("/api/files/describe", json=with_component)).status_code == 422
    assert (await client.post("/api/files/describe", json={"path": str(stl)})).status_code == 422  # a point is required
    assert (await client.get("/api/files/cad", params={"path": str(tmp_path / "nope.stl")})).status_code == 404
    (tmp_path / "notes.txt").write_text("x")
    assert (await client.get("/api/files/cad", params={"path": str(tmp_path / "notes.txt")})).status_code == 415


async def test_create_design_and_project(api: Any, tmp_path: Path) -> None:
    client, _ = api
    bad = await client.post(
        "/api/designs", json={"path": str(tmp_path / "x"), "create": {"name": "X", "backend": "nope"}}
    )
    assert bad.status_code == 422
    project = await client.post("/api/projects", json={"path": str(tmp_path / "proj")})
    assert ".claude/settings.json" in project.json()["created"] and ".codex/hooks.json" not in project.json()["created"]
    status = await client.get("/api/projects/agents", params={"path": str(tmp_path / "proj")})
    assert status.json() == {"hooks": False, "codex": False}  # not without the human's permission
    hooked = await client.post("/api/projects", json={"path": str(tmp_path / "proj"), "hooks": True})
    assert ".codex/hooks.json" in hooked.json()["created"]
    assert (await client.post("/api/projects", json={"path": "relative"})).status_code == 422


async def test_panel_webviews_may_call_the_api_and_other_pages_may_not(api: Any) -> None:
    client, _ = api
    preflight = {"Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type"}
    webview = "vscode-webview://1a2b3c4d"
    allowed = await client.options("/api/designs/x/selection", headers={"Origin": webview, **preflight})
    assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == webview
    other = await client.options("/api/designs/x/selection", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in other.headers
    assert (await client.get("/designs/x")).status_code == 404  # the server hosts no UI


def test_design_stream_replays_follows_and_resumes(settings: Any, fake_backend: Any, tmp_path: Path) -> None:
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    core = make_core(settings, fake_backend)
    state, _ = core.designs.open(fake_design(tmp_path / "box").path)
    client = TestClient(create_app(core))
    stream = f"/api/designs/{state.id}/stream"

    with client.websocket_connect(stream) as ws:
        opened = json.loads(ws.receive_text())
        assert opened["type"] == "design_opened"
        client.put(f"/api/designs/{state.id}/selection", json={"items": []})
        live = json.loads(ws.receive_text())
        assert live["type"] == "selection_changed" and live["seq"] > opened["seq"]

    client.put(f"/api/designs/{state.id}/selection", json={"items": []})  # while nobody listens
    with client.websocket_connect(f"{stream}?after={live['seq']}") as ws:  # a reconnect resumes after it
        missed = json.loads(ws.receive_text())
        assert missed["type"] == "selection_changed" and missed["seq"] == live["seq"] + 1

    with client.websocket_connect("/api/designs/dsg_missing/stream") as ws, pytest.raises(WebSocketDisconnect) as gone:
        ws.receive_text()
    assert gone.value.code == 4404


def test_activity_stream_reports_finished_builds(settings: Any, fake_backend: Any, tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    core = make_core(settings, fake_backend)
    state, _ = core.designs.open(fake_design(tmp_path / "box").path)
    client = TestClient(create_app(core))
    with client.websocket_connect("/api/activity") as ws:
        assert client.post(f"/api/designs/{state.id}/build").status_code == 201
        build = json.loads(ws.receive_text())
    assert {k: build[k] for k in ("id", "name", "revision", "status", "origin", "error")} == {
        "id": state.id, "name": "Box", "revision": 1, "status": "succeeded", "origin": "file", "error": None
    }  # fmt: skip


def test_context_hook_never_fails_the_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sent: list[urllib.request.Request] = []

    class Response(io.BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Response:
        sent.append(request)
        return Response(b"The human is pointing at these")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": "/work/proj", "session_id": "abc"})))
    assert cli.main(["context", "--hook", "codex", "--server", "http://h:9"]) == 0
    assert sent[0].full_url == "http://h:9/api/context/deliveries"
    assert json.loads(sent[0].data) == {"root": "/work/proj", "agent": "codex", "session": "abc"}  # type: ignore[arg-type]
    assert "pointing at these" in capsys.readouterr().out

    def down(request: urllib.request.Request, timeout: float) -> Response:
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", down)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": "/work/proj", "session_id": "abc"})))
    assert cli.main(["context", "--hook", "claude"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.main(["context", "--hook", "claude"]) == 0
    assert capsys.readouterr().out == ""
