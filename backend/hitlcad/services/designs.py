"""Designs bound to their folders, documents and event logs, shared by every surface: MCP tools, the panel's REST API
and the CLI.

A design is a folder holding ``design.json`` and its model source; hitlcad keeps its files as a document
(``documents``), of which the folder is the copy on disk, and every edit goes through `change` (made on a version,
merged into the current one) or `take_in` (what was written to the folder). The folder → design id mapping
lives in hitlcad's store on this machine, so machine-local ids never end up in the (committable) design
files. Building (``builds``), what the human points at (``feedback``) and the agent's context
(``context``) are services on top of this one.
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from pathlib import Path

from hitlcad.backends.registry import BackendRegistry
from hitlcad.config import DEFAULT_BACKEND
from hitlcad.core.design import DESIGN_FILE, DesignSpec
from hitlcad.core.events import (
    DesignOpened,
    DesignState,
    DesignUpdated,
    DocumentChanged,
    EditsCrossed,
    ErrorRaised,
    Event,
)
from hitlcad.core.report import Earlier, Revision
from hitlcad.core.types import new_id
from hitlcad.kernel.errors import NOT_FOUND_DESIGN, NOT_FOUND_REVISION, REQUEST_INVALID, STORE_UNREADABLE, HitlcadError
from hitlcad.services.bus import EventBus
from hitlcad.services.documents import Change, DocumentService
from hitlcad.store.base import DesignStore
from hitlcad.workspace.document import Disk, Edit, Snapshot
from hitlcad.workspace.folder import DesignFolder, load_design


def _absolute(path: str | Path) -> DesignFolder:
    """Agents and the panel run elsewhere than the server, so relative paths would be ambiguous."""
    if not Path(path).expanduser().is_absolute():
        raise REQUEST_INVALID(f"design path must be absolute, got {str(path)!r}")
    return DesignFolder(Path(path))


def readable(files: Snapshot) -> DesignSpec | None:
    """The design in its design.json, or None when it does not read (its next build reports why)."""
    try:
        return load_design(files.design)
    except HitlcadError:
        return None


class DesignService:
    def __init__(self, store: DesignStore, registry: BackendRegistry, loaded: int) -> None:
        self.store = store
        self.documents = DocumentService(store)
        self.registry = registry
        self.bus = EventBus()
        self.folders_changed = asyncio.Event()
        # designs a panel or an agent opened since the server started, each queued once for the watcher to bring
        # up to date: the others wait until they are opened, however many are registered
        self._used: set[str] = set()
        self.first_used: asyncio.Queue[str] = asyncio.Queue()
        self._folders = store.folders()
        self._cache: dict[str, DesignState] = {}
        self.loaded = loaded  # revisions kept in memory: the panel and the tools read the same few
        self._revisions: OrderedDict[tuple[str, int], Revision] = OrderedDict()
        self._lock = threading.Lock()  # revision() runs in worker threads too

    # ------------------------------------------------------------------ state & events

    def get(self, design_id: str) -> DesignState:
        state = self._cache.get(design_id)
        if state is None:
            if not self.store.exists(design_id):
                raise NOT_FOUND_DESIGN(f"design {design_id} not found")
            try:
                events = self.store.events(design_id)
            except HitlcadError as e:
                if e.code != STORE_UNREADABLE.code:
                    raise
                events = self._new_history(design_id)
            state = DesignState.replay(design_id, events)
            self._cache[design_id] = state
        return state

    def _new_history(self, design_id: str) -> list[Event]:
        """Another version of hitlcad wrote this design's history: set it aside, intact, and start a new one
        that says so. Its folder and design.json are untouched; the next build starts its revisions again."""
        path = next((p for p, i in self._folders.items() if i == design_id), None)
        if path is None:
            raise NOT_FOUND_DESIGN(f"design {design_id} has no folder")
        aside = self.store.set_aside(design_id)
        notice = STORE_UNREADABLE.diagnostic(f"This design's history was written by another version of hitlcad and "
                                             f"could not be read: it was set aside in {aside}. The next build starts a "
                                             "new history.")  # fmt: skip
        opened = DesignOpened(path=path, name=Path(path).name, backend=DEFAULT_BACKEND)  # sync() reads design.json
        return [self.store.append(design_id, e) for e in (opened, ErrorRaised(diagnostic=notice))]

    def revision(self, design_id: str, revision_id: int) -> Revision:
        """A revision in full: what it built from and its report. One that does not read sets the design's history
        aside, as events that do not read do."""
        key = (design_id, revision_id)
        with self._lock:
            found = self._revisions.get(key)
        if found is None:
            if self.get(design_id).revision(revision_id) is None:
                raise NOT_FOUND_REVISION(f"revision {revision_id} not found")
            try:
                found = self.store.revision(design_id, revision_id)
            except HitlcadError as e:
                if e.code != STORE_UNREADABLE.code:
                    raise
                self._cache[design_id] = DesignState.replay(design_id, self._new_history(design_id))
                raise
            assert found is not None  # its event lists it
        with self._lock:
            self._revisions[key] = found
            self._revisions.move_to_end(key)
            while len(self._revisions) > self.loaded:
                self._revisions.popitem(last=False)
        return found

    def earlier(self, design_id: str, revision_id: int, configuration: str | None) -> Earlier | None:
        """What the configuration's revision before this one failed."""
        before = self.get(design_id).before(revision_id, configuration)
        return Earlier(before.id, self.store.failing(design_id, before.id)) if before else None

    def add_revision(self, design_id: str, revision: Revision) -> Event:
        state = self.get(design_id)
        stored = self.store.add_revision(design_id, revision)
        with self._lock:
            self._revisions[(design_id, revision.id)] = revision
        state.apply(stored)
        self.bus.publish(design_id, stored)
        return stored

    def list(self) -> list[DesignState]:
        states = [self.get(i) for i in self.folders().values()]
        return sorted(states, key=lambda s: s.updated_at, reverse=True)

    def emit(self, design_id: str, event: Event) -> Event:
        state = self.get(design_id)  # raises not_found.design for unknown designs
        stored = self.store.append(design_id, event)
        state.apply(stored)
        self.bus.publish(design_id, stored)
        return stored

    def announce(self, design_id: str, event: Event) -> None:
        """A live event: applied and published, not kept (its seq stays 0)."""
        self.get(design_id).apply(event)
        self.bus.publish(design_id, event)

    # ------------------------------------------------------------------ folders

    def folders(self) -> dict[str, str]:
        """Registered folders that still exist: path -> design id."""
        return {p: i for p, i in self._folders.items() if self.store.exists(i) and Path(p).is_dir()}

    def id_for(self, path: str | Path) -> str | None:
        return self.folders().get(str(DesignFolder(Path(path)).path))

    def folder(self, design_id: str) -> DesignFolder:
        return DesignFolder(Path(self.get(design_id).path))

    def open(
        self, path: str | Path, backend: str | None = None, name: str | None = None
    ) -> tuple[DesignState, list[str]]:
        """Bind the folder at ``path`` to its design, its files becoming the design's document. With ``backend``, a
        folder that is not a design yet is scaffolded first. Returns the design and the files that were created.
        The folder watcher builds it once its model exists, and when first opened since the server started, if its
        files changed; a design.json that does not validate is opened all the same, and its build says why."""
        folder = _absolute(path)
        created: Disk | None = None
        if not folder.exists():
            if backend is None:
                raise NOT_FOUND_DESIGN(f"no {DESIGN_FILE} in {folder.path}")
            if backend not in self.registry.names():
                raise REQUEST_INVALID(f"unknown backend {backend!r}; available: {', '.join(self.registry.names())}")
            created = folder.scaffold(name or folder.path.name, backend)
        design_id = self.id_for(folder.path)
        if design_id is None:
            design_id = new_id("dsg")
            self.store.create(design_id)
            disk = created or Disk.read(folder.path)
            design = readable(Snapshot(0, disk.texts))
            self.emit(design_id, DesignOpened(path=str(folder.path), name=design.name if design else folder.path.name,
                                              backend=design.backend if design else DEFAULT_BACKEND))  # fmt: skip
            origin = "scaffold" if created else "folder"
            self._changed(design_id, self.documents.create(design_id, folder.path, disk, origin), origin, 0, "")
            self.store.bind(str(folder.path), design_id)
            self._folders[str(folder.path)] = design_id
            self.folders_changed.set()
            if str(folder.path) in self.store.set_aside_folders:
                self.emit(design_id, ErrorRaised(diagnostic=STORE_UNREADABLE.diagnostic(
                    "This design's history was written by another version of hitlcad: it was set aside with the "
                    "database it was in. Its revisions start again.")))  # fmt: skip
        self.sync(design_id)
        self._use(design_id)
        return self.get(design_id), [*created.texts, *created.sums] if created else []

    def ensure(self, path: str | Path) -> DesignState:
        """The design for ``path``, opening the folder if it is not registered yet."""
        design_id = self.id_for(_absolute(path).path)
        if design_id is None:
            return self.open(path)[0]
        self._use(design_id)
        return self.get(design_id)

    def _use(self, design_id: str) -> None:
        if design_id not in self._used:
            self._used.add(design_id)
            self.first_used.put_nowait(design_id)

    def where_used(self, design_id: str) -> list[str]:
        """Registered designs that use this one as a component, directly."""
        path = Path(self.get(design_id).path)
        out = []
        for folder, other in self.folders().items():
            try:
                if path in DesignFolder(Path(folder)).components(self.files(other)):
                    out.append(other)
            except HitlcadError:  # an unreadable design.json: its own build reports it
                continue
        return out

    def sync(self, design_id: str, design: DesignSpec | None = None) -> None:
        """Mirror the parts of design.json the panel shows (name, backend, brief) into the log."""
        state = self.get(design_id)
        design = design or readable(self.files(design_id))
        if design is None:  # the next build reports why
            return
        if (state.name, state.backend, state.brief) != (design.name, design.backend, design.brief):
            self.emit(design_id, DesignUpdated(name=design.name, backend=design.backend, brief=design.brief))

    # ------------------------------------------------------------------ files

    def files(self, design_id: str) -> Snapshot:
        """The design's files, as its document holds them now."""
        return self.documents.current(design_id)

    def files_at(self, path: Path) -> Snapshot | None:
        """The files of the design in a folder (registered when it was not yet); None when it holds none."""
        folder = DesignFolder(path)
        design_id = self.id_for(folder.path)
        if design_id is None:
            if not folder.exists():
                return None
            design_id = self.open(folder.path)[0].id
        return self.files(design_id)

    def change(self, design_id: str, base: int, origin: str, edit: Edit, summary: str = "") -> Change | None:
        """Make `edit` on the design's files as they were at version `base`, merged with what changed since."""
        change = self.documents.change(design_id, Path(self.get(design_id).path), base, origin, edit)
        return self._changed(design_id, change, origin, base, summary)

    async def take_in(self, design_id: str, origin: str = "disk", base: int | None = None) -> Change | None:
        """What the design's folder holds now, written by `origin` from version `base` (unknown: the version last
        written there), merged into the design's files."""
        folder = Path(self.get(design_id).path)
        disk = await asyncio.to_thread(Disk.read, folder)
        since = self.files(design_id).version if base is None else base
        return self._changed(
            design_id, self.documents.take_in(design_id, folder, disk, base, origin), origin, since, ""
        )

    def _changed(self, design_id: str, change: Change | None, origin: str, base: int, summary: str) -> Change | None:
        if change is None:
            return None
        others = sorted({c.origin for c in self.get(design_id).changes if c.version > base} - {origin})
        self.emit(
            design_id, DocumentChanged(version=change.version, origin=origin, files=change.files, summary=summary)
        )
        if change.crossings:
            self.emit(design_id, EditsCrossed(version=change.version, origin=origin, others=others,
                                              crossed=change.crossings))  # fmt: skip
        return change
