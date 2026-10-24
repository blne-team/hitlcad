"""The REST API and event streams the VS Code panel and extension use, the file viewer, and the CLI hook."""

from __future__ import annotations

import io
import json
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import trimesh

from hitlcad import cli
from hitlcad.api.app import create_app
from hitlcad.app import HitlCad

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

    found = (await api.get("/api/catalog", params={"query": "M3 heat set insert"})).json()
    assert found and found[0]["family"] == "insert"


async def test_model_files_for_the_file_viewer(api: httpx.AsyncClient, tmp_path: Path) -> None:
    stl = tmp_path / "cube.stl"
    trimesh.creation.box((10, 20, 30)).export(stl)
    view = await api.get("/api/files/cad", params={"path": str(stl)})
    assert view.status_code == 200 and view.json()["shapes"]["bb"]["zmax"] == pytest.approx(15)
    picked = await api.post("/api/files/describe", json={"path": str(stl), "point": [5, 0, 0]})
    assert picked.json()["label"].startswith("Point on body 1 of 1")
    assert (await api.get("/api/files/cad", params={"path": str(tmp_path / "a.txt")})).status_code == 404


async def test_panel_webviews_may_call_the_api_and_other_pages_may_not(api: httpx.AsyncClient) -> None:
    ok = await api.get("/api/health", headers={"Origin": "vscode-webview://abc123"})
    assert ok.headers["access-control-allow-origin"] == "vscode-webview://abc123"
    other = await api.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


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


def test_the_context_hook_never_fails_the_prompt(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": "/x", "session_id": "s"})))

    def down(*_: Any, **__: Any) -> Any:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert cli.main(["context", "--hook", "claude", "--server", "http://127.0.0.1:9"]) == 0
    assert capsys.readouterr().out == ""
