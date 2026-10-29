"""HTTP surface: REST and WebSocket event streams for the VS Code panel and extension, MCP at /mcp for coding
agents."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
from collections.abc import AsyncIterator
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, TypeVar

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, TypeAdapter
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from hitlcad.api.mcp import build_mcp
from hitlcad.app import HitlCad
from hitlcad.backends.base import BackendInfo
from hitlcad.config import DEFAULT_BACKEND, MCP_PATH
from hitlcad.core.events import DesignState, Event, RevisionCreated
from hitlcad.core.report import BuildStatus, Origin, Result, Revision
from hitlcad.core.types import Pick, Pin, PinMotion, SelectionItem
from hitlcad.geometry import cadview
from hitlcad.kernel.errors import (
    FILE_UNREADABLE,
    NOT_FOUND_FILE,
    PROJECT_CONFIG,
    REQUEST_INVALID,
    Diagnostic,
    ErrorKind,
    HitlcadError,
    catalog,
    diagnose,
    http_status,
)
from hitlcad.kernel.plugins import Plugin, registry
from hitlcad.review import selection
from hitlcad.services.bus import DROPPED
from hitlcad.workspace import agents, project

T = TypeVar("T")
log = logging.getLogger(__name__)

WEBVIEW_ORIGIN = r"vscode-webview://[^/]+"  # the panel UI runs in VS Code webviews


class OriginCheck:
    """Refuses requests and WebSocket handshakes from a web page whose origin may not call the API. CORS only
    keeps such a page from reading responses: it still sends them, and WebSockets are not subject to it.
    Clients that are not browsers (the extension host, agents, the hook) send no Origin."""

    def __init__(self, app: ASGIApp, origins: list[str]) -> None:
        self.app, self.origins = app, origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        origin = Headers(scope=scope).get("origin") if scope["type"] in ("http", "websocket") else None
        if origin is None or origin in self.origins or re.fullmatch(WEBVIEW_ORIGIN, origin):
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await WebSocketClose(status.WS_1008_POLICY_VIOLATION, "origin not allowed")(scope, receive, send)
        else:
            await PlainTextResponse("origin not allowed", status_code=403)(scope, receive, send)


async def _until_gone(socket: WebSocket, queue: asyncio.Queue[T]) -> AsyncIterator[T]:
    """Items from `queue` until the client disconnects. Event streams are one-way: clients send nothing, so
    the socket's next message is its close. WebSockets, unlike long-lived HTTP responses, do not take one of
    the browser's six connections per server, so any number of panels stay live. A subscriber the bus
    dropped for falling behind is closed, and its client reconnects from the last event it saw."""
    gone = asyncio.ensure_future(socket.receive())
    try:
        while True:
            item = asyncio.ensure_future(queue.get())
            await asyncio.wait({item, gone}, return_when=asyncio.FIRST_COMPLETED)
            if gone.done():
                item.cancel()
                return
            if item.result() is DROPPED:
                await socket.close(status.WS_1013_TRY_AGAIN_LATER, "fell behind; reconnect from the last event")
                return
            yield item.result()
    finally:
        gone.cancel()


class CreateDesign(BaseModel):
    name: str
    backend: str = DEFAULT_BACKEND


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


class MeasureSelection(BaseModel):
    revision: int
    items: tuple[Pick, Pick]


class AddPin(Pick):
    note: str = ""
    revision: int | None = None
    motion: PinMotion | None = Field(default=None, description="Where a range of motion was in the view")


class WatchMotion(BaseModel):
    revision: int = Field(description="The revision whose motion was watched")
    key: str = Field(description="Its loop sweep's (`MotionRange.key`)")


class UpdatePin(BaseModel):
    note: str | None = None
    resolved: bool | None = None


class EditParameters(BaseModel):
    values: dict[str, Any]
    expected: dict[str, Any] = Field(description="The values the edit was made on, of the parameters it changes: "
                                     "where design.json now has others, the edit is refused (409)")  # fmt: skip


class EditOutputs(BaseModel):
    outputs: list[str] = Field(description="What each build is to write: design.json `outputs`")
    expected: list[str] = Field(description="The outputs the edit was made on: where design.json now has others, "
                                "the edit is refused (409)")  # fmt: skip


class BuildRequest(BaseModel):
    configuration: str | None = Field(
        default=None, description="A named configuration; default: the parameters as written"
    )


class Structure(BaseModel):
    overview: str
    graph: dict[str, Any]


class AnalysisInfo(BaseModel):
    name: str = Field(description="What its results are filed under (`Result.analysis`)")
    title: str
    summary: str


class OutputInfo(BaseModel):
    name: str = Field(description="What design.json `outputs` calls it")
    title: str = Field(description="What its files are, for the panel's file list; empty for the preview")
    summary: str


class Capabilities(BaseModel):
    version: str
    plugins: list[Plugin] = Field(description="Every plugin loaded, hitlcad's own among them")
    plugin_problems: list[Diagnostic] = Field(description="Plugins that did not load, failed or clash")
    backends: list[BackendInfo]
    analyses: list[AnalysisInfo] = Field(description="In the order they run: cause before effect")
    outputs: list[OutputInfo] = Field(description="What a build writes when design.json `outputs` names it")
    error_kinds: list[ErrorKind] = Field(
        description="Every result's and error's kind, by code: its title and usual fix"
    )
    python: str = Field(description="The interpreter that builds models: part files resolve their imports in it")


class DesignSummary(BaseModel):
    id: str
    path: str
    name: str
    backend: str
    revisions: int
    open_pins: int
    building: bool
    error: Diagnostic | None = Field(description="What last stopped its work, when that was not the design's to fix")


class OpenedDesign(DesignSummary):
    created: bool
    files: list[str] = Field(description="What creating it wrote")


class ProjectCreated(BaseModel):
    path: str
    created: list[str] = Field(description="The files written or brought up to date")


class BuildActivity(DesignSummary):
    """A build that finished, one message of the /api/activity WebSocket."""

    revision: int
    status: BuildStatus
    origin: Origin
    failure: Result | None = Field(description="What a failed build failed on: the result that leads its first problem")


class ErrorBody(BaseModel):
    """Every error response: what it says, and the diagnostic it comes from."""

    detail: str
    diagnostic: Diagnostic


#: The WebSockets' messages and the error body, which OpenAPI has no routes for, as schema components: the panel and
#: the extension read their types from the schema as they do every route's.
MESSAGES = {"DesignEvent": Event, "BuildActivity": BuildActivity, "ErrorBody": ErrorBody}


def design_summary(s: DesignState) -> DesignSummary:
    return DesignSummary(id=s.id, path=s.path, name=s.name, backend=s.backend, revisions=len(s.revisions),
                         open_pins=len(s.open_pins), building=s.building is not None, error=s.error)  # fmt: skip


def create_app(core: HitlCad | None = None) -> FastAPI:
    core = core or HitlCad.create()
    designs, builds, feedback, settings = core.designs, core.builds, core.feedback, core.settings
    mcp = build_mcp(core)
    # The middleware below checks Host and Origin for /mcp as for every route, from one list of each.
    unchecked = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp_app = mcp.streamable_http_app(streamable_http_path=MCP_PATH, transport_security=unchecked)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        core.start()
        async with mcp.session_manager.run():
            yield
        await core.shutdown()

    app = FastAPI(title="hitlcad", version=version("hitlcad"), lifespan=lifespan)
    app.state.core = core
    routes_schema = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            components = routes_schema()["components"]["schemas"]
            for name, kind in MESSAGES.items():
                found = TypeAdapter(kind).json_schema(mode="serialization", ref_template="#/components/schemas/{model}")
                for model, schema in found.pop("$defs", {}).items():
                    components.setdefault(model, schema)
                components[name] = found
        return app.openapi_schema  # type: ignore[return-value]

    app.openapi = openapi  # type: ignore[method-assign]
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_origin_regex=WEBVIEW_ORIGIN,
                       allow_methods=["*"], allow_headers=["*"])  # fmt: skip
    app.add_middleware(OriginCheck, origins=settings.cors_origins)
    # A web page can rebind its own domain to 127.0.0.1 and call the API as its own origin, past CORS. Only
    # requests addressed to this machine, or to the URL agents are given, reach the API.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.hosts)

    def error(d: Diagnostic) -> JSONResponse:
        body = ErrorBody(detail=d.message, diagnostic=d)
        return JSONResponse(body.model_dump(mode="json"), status_code=http_status(d))

    @app.exception_handler(HitlcadError)
    async def diagnosed(_: Request, e: HitlcadError) -> JSONResponse:
        return error(e.diagnostic)

    @app.exception_handler(Exception)
    async def bug(_: Request, e: Exception) -> JSONResponse:
        log.exception("request failed")
        return error(diagnose(e))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/capabilities")
    async def capabilities() -> Capabilities:
        analyses = [AnalysisInfo(name=a.name, title=a.title, summary=a.summary) for a in core.pipeline.analyses]
        outputs = [
            OutputInfo(name=n, title=title, summary=summary) for n, (title, summary) in core.pipeline.outputs.items()
        ]
        kinds = sorted(catalog().values(), key=lambda k: k.code)
        found = registry()
        return Capabilities(version=version("hitlcad"), plugins=list(found.plugins.values()), plugin_problems=found.failures,
                            backends=core.registry.describe(), analyses=analyses, outputs=outputs, error_kinds=kinds,
                            python=sys.executable)  # fmt: skip

    # ------------------------------------------------------------------ designs

    @app.get("/api/designs")
    async def list_designs() -> list[DesignSummary]:
        return [design_summary(s) for s in designs.list()]

    @app.post("/api/designs")
    async def open_design(body: OpenDesign) -> OpenedDesign:
        new = body.create
        s, created = designs.open(body.path, new.backend, new.name) if new else designs.open(body.path)
        return OpenedDesign(**design_summary(s).model_dump(), created=bool(created), files=created)

    @app.get("/api/designs/{design_id}")
    async def get_design(design_id: str) -> DesignState:
        return designs.get(design_id)

    @app.websocket("/api/designs/{design_id}/stream")
    async def stream(socket: WebSocket, design_id: str, after: int = 0) -> None:
        """The design's events after `after`, then live ones, one JSON event per message; a client that lost
        the connection reconnects with the last `seq` it saw (a live-only event's is 0)."""
        await socket.accept()
        try:
            designs.get(design_id)
        except HitlcadError as e:
            await socket.close(code=4404, reason=e.diagnostic.message)
            return
        queue = designs.bus.subscribe(design_id)
        try:
            cursor = after
            for event in designs.store.events(design_id, cursor):  # replay, then live
                cursor = event.seq
                await socket.send_text(event.model_dump_json())
            async for event in _until_gone(socket, queue):
                if not event.seq or event.seq > cursor:  # live only (`DesignService.announce`), or not yet sent
                    cursor = max(cursor, event.seq)
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
                rev = event.revision
                activity = BuildActivity(**design_summary(designs.get(design_id)).model_dump(), revision=rev.id,
                                         status=rev.status, origin=rev.origin, failure=rev.failure)  # fmt: skip
                await socket.send_text(activity.model_dump_json())
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

    @app.post("/api/designs/{design_id}/parameters", status_code=201)
    async def edit_parameters(design_id: str, body: EditParameters) -> Revision:
        return await builds.edit_parameters(design_id, body.values, body.expected)

    @app.post("/api/designs/{design_id}/outputs", status_code=201)
    async def edit_outputs(design_id: str, body: EditOutputs) -> Revision:
        return await builds.edit_outputs(design_id, body.outputs, body.expected)

    @app.get("/api/designs/{design_id}/revisions/{revision_id}")
    async def get_revision(design_id: str, revision_id: int) -> Revision:
        """A revision in full: what it built from and its report. The design's state lists them."""
        return await asyncio.to_thread(designs.revision, design_id, revision_id)

    @app.get("/api/designs/{design_id}/revisions/{revision_id}/files/{path:path}")
    async def revision_file(design_id: str, revision_id: int, path: str) -> FileResponse:
        """A revision's file: one of its artifacts, or a part view its scene draws (its components' included); of
        the revision being built, its scene once shown (`BuildProgress.scene`) and its part views."""
        running = designs.get(design_id).building
        building = running if running is not None and running.revision == revision_id else None
        rev = None if building else designs.revision(design_id, revision_id)  # raises for one that does not exist
        root = designs.store.revision_dir(design_id, revision_id).resolve()
        if building is not None:
            if path == building.scene:
                return FileResponse(root / path, media_type="application/json")
        elif rev is not None:
            artifact = next((a for a in rev.report.artifacts if a.path == path), None)
            if artifact is not None:
                return FileResponse(root / artifact.path, media_type=artifact.media_type, filename=artifact.name,
                                    content_disposition_type="inline")  # fmt: skip
        view = (root / path).resolve()
        if path.endswith(cadview.VIEW_SUFFIX) and view.is_relative_to(root) and view.is_file():
            return FileResponse(view, media_type="application/octet-stream")
        raise NOT_FOUND_FILE(f"revision {revision_id} has no file {path}")

    # ------------------------------------------------------------------ selection & pins

    @app.post("/api/designs/{design_id}/measure")
    async def measure_selection(design_id: str, body: MeasureSelection) -> dict[str, str]:
        return {"text": await feedback.measure(design_id, body.revision, body.items)}

    @app.put("/api/designs/{design_id}/selection")
    async def set_selection(design_id: str, body: SetSelection) -> dict[str, list[SelectionItem]]:
        return {"items": await feedback.set_selection(design_id, body.items, body.revision)}

    @app.post("/api/designs/{design_id}/pins", status_code=201)
    async def add_pin(design_id: str, body: AddPin) -> Pin:
        pick = Pick(point=body.point, normal=body.normal, component=body.component)
        return await feedback.add_pin(design_id, pick, body.note, body.revision, body.motion)

    @app.patch("/api/designs/{design_id}/pins/{pin_id}")
    async def update_pin(design_id: str, pin_id: str, body: UpdatePin) -> Pin:
        return feedback.update_pin(design_id, pin_id, note=body.note, resolved=body.resolved)

    @app.delete("/api/designs/{design_id}/pins/{pin_id}", status_code=204)
    async def remove_pin(design_id: str, pin_id: str) -> None:
        feedback.remove_pin(design_id, pin_id)

    @app.post("/api/designs/{design_id}/watched")
    async def watch_motion(design_id: str, body: WatchMotion) -> Revision:
        """The human watched a loop's motion move: its result passes, resting on that, from the build this starts."""
        return await builds.watch_motion(design_id, body.revision, body.key)

    @app.delete("/api/designs/{design_id}/watched/{key}")
    async def unwatch_motion(design_id: str, key: str, revision: int) -> Revision:
        return await builds.watch_motion(design_id, revision, key, watched=False)

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
            raise REQUEST_INVALID(f"project path must be absolute, got {raw!r}")
        return path.resolve()

    @app.post("/api/projects")
    async def init_project(body: InitProject) -> ProjectCreated:
        path = project_path(body.path)
        try:
            created = project.init_project(path, settings, sys.executable, core.registry.all(),
                                           hooks=body.hooks, codex=body.codex)  # fmt: skip
        except ValueError as e:  # a config file of the human's that is not plain JSON
            raise PROJECT_CONFIG(str(e)) from None
        return ProjectCreated(path=str(path), created=created)

    @app.get("/api/projects/agents")
    async def agent_setup(path: str) -> agents.AgentSetup:
        """Whether the project has the agents' prompt hooks and Codex's config, so the extension asks
        the human about what is missing."""
        return agents.status(project_path(path))

    def model_file(raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise NOT_FOUND_FILE(f"no file {raw}")
        if path.suffix.lower() not in cadview.VIEWABLE:
            raise REQUEST_INVALID(f"unsupported file type {path.suffix}")
        return path.resolve()

    @app.get("/api/files/cad")
    async def cad_file(path: str) -> RedirectResponse:
        """A model file tessellated for the 3D file viewer: to its scene, whose part views lie beside it."""
        source = model_file(path)
        try:
            key = await asyncio.to_thread(cadview.cached_scene, source, settings.cache_dir / "cad",
                                          settings.mesh_tolerance_mm, settings.mesh_angular_tolerance)  # fmt: skip
        except Exception as e:  # any file: its reader's error, whatever its kind, is what the panel shows
            log.exception("could not convert %s", source)
            raise FILE_UNREADABLE(f"could not convert {source.name}: {e}") from None
        return RedirectResponse(f"/api/files/cad/{key}/{cadview.SCENE_FILE}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/api/files/cad/{key}/{name}")
    async def cad_scene_file(key: str, name: str) -> FileResponse:
        """A file of a model file's scene: the scene, or one of its part views."""
        file = settings.cache_dir / "cad" / key / name
        names = rf"{re.escape(cadview.SCENE_FILE)}|\d+{re.escape(cadview.VIEW_SUFFIX)}"
        if not (re.fullmatch(r"[0-9a-f]{24}", key) and re.fullmatch(names, name) and file.is_file()):
            raise NOT_FOUND_FILE(f"no scene file {key}/{name}")
        media = "application/json" if name == cadview.SCENE_FILE else "application/octet-stream"
        return FileResponse(file, media_type=media)

    @app.post("/api/files/describe")
    async def describe_file(body: DescribeFilePick) -> SelectionItem:
        source = model_file(body.path)
        return await asyncio.to_thread(selection.describe_file_pick, source, body.point, body.normal, body.component,
                                       settings.mesh_tolerance_mm, settings.mesh_angular_tolerance)  # fmt: skip

    app.router.routes.extend(mcp_app.routes)  # serves exactly /mcp; lifespan above runs its manager
    return app
