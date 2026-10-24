"""HTTP surface: REST and WebSocket event streams for the VS Code panel and extension, MCP at /mcp for coding
agents."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import AsyncIterator
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from hitlcad import catalogs
from hitlcad.analysis import cadview, selection
from hitlcad.api.mcp_server import build_mcp
from hitlcad.app import HitlCad
from hitlcad.backends.base import BackendInfo
from hitlcad.core.events import DesignState, Event, RevisionCreated
from hitlcad.core.report import Revision
from hitlcad.core.types import Pick, Pin, SelectionItem
from hitlcad.designs import agents, project
from hitlcad.service.designs import NotFoundError

T = TypeVar("T")
log = logging.getLogger(__name__)


async def _until_gone(socket: WebSocket, queue: asyncio.Queue[T]) -> AsyncIterator[T]:
    """Items from `queue` until the client disconnects. Event streams are one-way: clients send nothing, so
    the socket's next message is its close. WebSockets, unlike long-lived HTTP responses, do not take one of
    the browser's six connections per server, so any number of panels stay live."""
    gone = asyncio.ensure_future(socket.receive())
    try:
        while True:
            item = asyncio.ensure_future(queue.get())
            await asyncio.wait({item, gone}, return_when=asyncio.FIRST_COMPLETED)
            if gone.done():
                item.cancel()
                return
            yield item.result()
    finally:
        gone.cancel()


class CreateDesign(BaseModel):
    name: str
    backend: str = "build123d"


class OpenDesign(BaseModel):
    path: str
    create: CreateDesign | None = Field(default=None, description="Scaffold the folder when it is not a design yet")


class ProjectPath(BaseModel):
    path: str


class InitProject(ProjectPath):
    hooks: bool = Field(default=False, description="The human allowed the agents' prompt hooks")
    codex: bool = Field(default=False, description="The human allowed changing Codex's config.toml")


class Delivery(BaseModel):
    root: str = Field(description="The agent's working directory: designs under it count")
    agent: Literal["claude", "codex"]
    session: str


class SetSelection(BaseModel):
    revision: int | None = None
    items: list[Pick] = Field(default_factory=list)


class DescribeFilePick(Pick):
    path: str


class AddPin(Pick):
    note: str = ""
    revision: int | None = None


class UpdatePin(BaseModel):
    note: str | None = None
    resolved: bool | None = None


class EditParameters(BaseModel):
    values: dict[str, Any]


class BuildRequest(BaseModel):
    configuration: str | None = Field(
        default=None, description="A named configuration; default: the parameters as written"
    )


class Structure(BaseModel):
    overview: str
    graph: dict[str, Any]


class Capabilities(BaseModel):
    version: str
    backends: list[BackendInfo]
    mcp_url: str


def design_summary(s: DesignState) -> dict[str, Any]:
    return {"id": s.id, "path": s.path, "name": s.name, "backend": s.backend, "revisions": len(s.revisions),
            "open_pins": len(s.open_pins), "building": s.building, "error": s.error}  # fmt: skip


def create_app(core: HitlCad | None = None) -> FastAPI:
    core = core or HitlCad.create()
    designs, builds, feedback, settings = core.designs, core.builds, core.feedback, core.settings
    mcp = build_mcp(core)
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", host=settings.host)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        core.start()
        async with mcp.session_manager.run():
            yield
        await core.shutdown()

    app = FastAPI(title="hitlcad", version=version("hitlcad"), lifespan=lifespan)
    app.state.core = core
    # The panel UI runs in VS Code webviews (origin vscode-webview://…) and calls the API from there.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_origin_regex=r"vscode-webview://[^/]+",
                       allow_methods=["*"], allow_headers=["*"])  # fmt: skip
    # A web page can rebind its own domain to 127.0.0.1 and call the API as its own origin, past CORS. Only
    # requests addressed to this machine, or to the URL agents are given, reach the API.
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["127.0.0.1", "localhost", "::1", urlsplit(settings.base_url).hostname])  # fmt: skip

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, e: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/capabilities")
    async def capabilities() -> Capabilities:
        return Capabilities(version=version("hitlcad"), backends=core.registry.describe(), mcp_url=settings.mcp_url)

    # ------------------------------------------------------------------ designs

    @app.get("/api/designs")
    async def list_designs() -> list[dict[str, Any]]:
        return [design_summary(s) for s in designs.list()]

    @app.post("/api/designs")
    async def open_design(body: OpenDesign) -> dict[str, Any]:
        new = body.create
        s, created = designs.open(body.path, new.backend, new.name) if new else designs.open(body.path)
        return {**design_summary(s), "created": bool(created), "files": created}

    @app.get("/api/designs/{design_id}")
    async def get_design(design_id: str) -> DesignState:
        return designs.get(design_id)

    @app.get("/api/designs/{design_id}/events")
    async def list_events(design_id: str, after: int = 0) -> list[Event]:
        designs.get(design_id)
        return designs.store.events(design_id, after)

    @app.websocket("/api/designs/{design_id}/stream")
    async def stream(socket: WebSocket, design_id: str, after: int = 0) -> None:
        """The design's events after `after`, then live ones, one JSON event per message; a client that lost
        the connection reconnects with the last `seq` it saw."""
        await socket.accept()
        try:
            designs.get(design_id)
        except NotFoundError as e:
            await socket.close(code=4404, reason=str(e))
            return
        queue = designs.bus.subscribe(design_id)
        try:
            cursor = after
            for event in designs.store.events(design_id, cursor):  # replay, then live
                cursor = event.seq
                await socket.send_text(event.model_dump_json())
            async for event in _until_gone(socket, queue):
                if event.seq > cursor:
                    cursor = event.seq
                    await socket.send_text(event.model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            designs.bus.unsubscribe(design_id, queue)

    @app.websocket("/api/activity")
    async def activity(socket: WebSocket) -> None:
        """Builds across all designs as they finish (not replayed), one JSON message each, so the extension
        can bring the panel up."""
        await socket.accept()
        queue = designs.bus.subscribe_all()
        try:
            async for design_id, event in _until_gone(socket, queue):
                if not isinstance(event, RevisionCreated):
                    continue
                s, rev = designs.get(design_id), event.revision
                await socket.send_text(json.dumps({
                    **design_summary(s), "revision": rev.id, "status": rev.report.status.value,
                    "origin": rev.origin, "error": rev.report.error}))  # fmt: skip
        except WebSocketDisconnect:
            pass
        finally:
            designs.bus.unsubscribe_all(queue)

    @app.post("/api/designs/{design_id}/build", status_code=201)
    async def build_design(design_id: str, body: BuildRequest | None = None) -> Revision:
        return await builds.build(design_id, "Built on request", configuration=body.configuration if body else None)

    @app.get("/api/designs/{design_id}/structure")
    async def structure(design_id: str, configuration: str | None = None) -> Structure:
        """The design graph for the panel's Structure view, and the overview an agent would read."""
        graph = await asyncio.to_thread(core.explore.graph, design_id, configuration)
        return Structure(overview=graph.overview(), graph=graph.structure())

    @app.get("/api/catalog")
    async def catalog(query: str, limit: int = 20) -> list[catalogs.CatalogItem]:
        return await asyncio.to_thread(catalogs.search, query, limit)

    @app.post("/api/designs/{design_id}/parameters", status_code=201)
    async def edit_parameters(design_id: str, body: EditParameters) -> Revision:
        return await builds.edit_parameters(design_id, body.values)

    @app.get("/api/designs/{design_id}/revisions/{revision_id}/files/{path:path}")
    async def revision_file(design_id: str, revision_id: int, path: str) -> FileResponse:
        rev = designs.get(design_id).revision(revision_id)
        artifact = next((a for a in rev.report.artifacts if a.path == path), None) if rev else None
        if artifact is None:
            raise HTTPException(404, "file not found")
        file = designs.store.revision_dir(design_id, revision_id) / artifact.path
        return FileResponse(file, media_type=artifact.media_type, filename=artifact.name,
                            content_disposition_type="inline")  # fmt: skip

    # ------------------------------------------------------------------ selection & pins

    @app.put("/api/designs/{design_id}/selection")
    async def set_selection(design_id: str, body: SetSelection) -> dict[str, list[SelectionItem]]:
        return {"items": await feedback.set_selection(design_id, body.items, body.revision)}

    @app.post("/api/designs/{design_id}/pins", status_code=201)
    async def add_pin(design_id: str, body: AddPin) -> Pin:
        pick = Pick(point=body.point, normal=body.normal, component=body.component)
        return await feedback.add_pin(design_id, pick, body.note, body.revision)

    @app.patch("/api/designs/{design_id}/pins/{pin_id}")
    async def update_pin(design_id: str, pin_id: str, body: UpdatePin) -> Pin:
        return feedback.update_pin(design_id, pin_id, note=body.note, resolved=body.resolved)

    @app.delete("/api/designs/{design_id}/pins/{pin_id}", status_code=204)
    async def remove_pin(design_id: str, pin_id: str) -> None:
        feedback.remove_pin(design_id, pin_id)

    @app.get("/api/context", response_class=PlainTextResponse)
    async def context(root: str | None = None) -> str:
        """Selections and open pins of the designs under ``root``, as text to paste into an agent's chat."""
        return core.context.read(root)

    @app.post("/api/context/deliveries", response_class=PlainTextResponse)
    async def deliver_context(body: Delivery) -> str:
        """Context for an agent session's next prompt (what its hitlcad hook adds): what changed since
        that session was last told, in full, and a reminder of the rest."""
        return core.context.deliver(body.root, body.agent, body.session)

    # ------------------------------------------------------------------ projects & loose model files

    def project_path(raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise HTTPException(422, "project path must be absolute")
        return path.resolve()

    @app.post("/api/projects")
    async def init_project(body: InitProject) -> dict[str, Any]:
        path = project_path(body.path)
        created = project.init_project(path, settings.base_url, sys.executable, core.registry.all(),
                                       hooks=body.hooks, codex=body.codex)  # fmt: skip
        return {"path": str(path), "created": created}

    @app.get("/api/projects/agents")
    async def agent_setup(path: str) -> agents.AgentSetup:
        """Whether the project has the agents' prompt hooks and Codex's config, so the extension asks
        the human about what is missing."""
        return agents.status(project_path(path))

    def model_file(raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise HTTPException(404, "file not found")
        if path.suffix.lower() not in cadview.VIEWABLE:
            raise HTTPException(415, f"unsupported file type {path.suffix}")
        return path.resolve()

    @app.get("/api/files/cad")
    async def cad_file(path: str) -> FileResponse:
        """A model file tessellated for the 3D file viewer (three-cad-viewer's instanced format)."""
        source = model_file(path)
        try:
            view = await asyncio.to_thread(cadview.cached_view, source, settings.data_dir / "cache" / "cad")
        except Exception as e:  # any file: its reader's error, whatever its kind, is what the panel shows
            log.exception("could not convert %s", source)
            raise HTTPException(422, f"could not convert {source.name}: {e}") from None
        return FileResponse(view, media_type="application/json")

    @app.post("/api/files/describe")
    async def describe_file(body: DescribeFilePick) -> SelectionItem:
        source = model_file(body.path)
        try:
            return await asyncio.to_thread(
                selection.describe_file_pick, source, body.point, body.normal, body.component
            )
        except (LookupError, ValueError) as e:  # no such part, or a mesh asked for a face
            raise HTTPException(422, f"could not describe the selection: {e}") from None

    app.router.routes.extend(mcp_app.routes)  # serves exactly /mcp; lifespan above runs its manager
    return app
