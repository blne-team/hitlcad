"""Design folders bound to their event logs, shared by every surface: MCP tools, the panel's REST API and the CLI.

A design is a folder holding ``design.json`` and its model source. The folder → design id mapping
lives in ``<data_dir>/folders.json``, so machine-local ids never end up in the (committable) design
files. Building (``builds``), what the human points at (``feedback``) and the agent's context
(``context``) are services on top of this one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hitlcad.backends.registry import BackendRegistry
from hitlcad.config import DEFAULT_BACKEND
from hitlcad.core.design import DesignSpec
from hitlcad.core.events import DesignOpened, DesignState, DesignUpdated, ErrorRaised, Event
from hitlcad.core.types import new_id
from hitlcad.designs.folder import DESIGN_FILE, DesignFolder
from hitlcad.service.bus import EventBus
from hitlcad.store.base import DesignStore, UnreadableHistory


class NotFoundError(LookupError):
    pass


class DesignError(ValueError):
    """What was asked cannot be done with the design's files as they are, or not as asked: the caller's to
    fix (the API answers 422), not a bug."""


def _absolute(path: str | Path) -> DesignFolder:
    """Agents and the panel run elsewhere than the server, so relative paths would be ambiguous."""
    if not Path(path).expanduser().is_absolute():
        raise DesignError(f"design path must be absolute, got {str(path)!r}")
    return DesignFolder(Path(path))


def load(folder: DesignFolder) -> DesignSpec:
    """The folder's design.json; a DesignError says what is wrong with it."""
    try:
        return folder.load()
    except ValueError as e:
        raise DesignError(str(e)) from None


class DesignService:
    def __init__(self, store: DesignStore, registry: BackendRegistry, folders_file: Path) -> None:
        self.store = store
        self.registry = registry
        self.bus = EventBus()
        self.folders_file = folders_file
        self.folders_changed = asyncio.Event()
        self._folders: dict[str, str] = (
            json.loads(folders_file.read_text(encoding="utf-8")) if folders_file.is_file() else {}
        )
        self._cache: dict[str, DesignState] = {}

    # ------------------------------------------------------------------ state & events

    def get(self, design_id: str) -> DesignState:
        state = self._cache.get(design_id)
        if state is None:
            if not self.store.exists(design_id):
                raise NotFoundError(f"design {design_id} not found")
            try:
                events = self.store.events(design_id)
            except UnreadableHistory:
                events = self._new_history(design_id)
            state = DesignState.replay(design_id, events)
            self._cache[design_id] = state
        return state

    def _new_history(self, design_id: str) -> list[Event]:
        """Another version of hitlcad wrote this design's history: set it aside, intact, and start a new one
        that says so. Its folder and design.json are untouched; the next build starts its revisions again."""
        path = next((p for p, i in self._folders.items() if i == design_id), None)
        if path is None:
            raise NotFoundError(f"design {design_id} has no folder")
        aside = self.store.set_aside(design_id)
        notice = (f"This design's history was written by another version of hitlcad and could not be read: it was "
                  f"set aside in {aside}. The next build starts a new history.")  # fmt: skip
        opened = DesignOpened(path=path, name=Path(path).name, backend=DEFAULT_BACKEND)  # sync() reads design.json
        return [self.store.append(design_id, e) for e in (opened, ErrorRaised(message=notice))]

    def list(self) -> list[DesignState]:
        states = [self.get(i) for i in self.folders().values()]
        return sorted(states, key=lambda s: s.updated_at, reverse=True)

    def emit(self, design_id: str, event: Event) -> Event:
        state = self.get(design_id)  # raises NotFoundError for unknown designs
        stored = self.store.append(design_id, event)
        state.apply(stored)
        self.bus.publish(design_id, stored)
        return stored

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
        """Bind the folder at ``path`` to its design. With ``backend``, a folder that is not a design yet
        is scaffolded first. Returns the design and the files that were created. The folder watcher
        builds it once its model exists."""
        folder = _absolute(path)
        created = []
        if not folder.exists():
            if backend is None:
                raise NotFoundError(f"no {DESIGN_FILE} in {folder.path}")
            if backend not in self.registry.names():
                raise DesignError(f"unknown backend {backend!r}; available: {', '.join(self.registry.names())}")
            created = folder.scaffold(name or folder.path.name, backend)
        design = load(folder)
        design_id = self.id_for(folder.path)
        if design_id is None:
            design_id = new_id("dsg")
            self.store.create(design_id)
            self.emit(design_id, DesignOpened(path=str(folder.path), name=design.name, backend=design.backend))
            self._folders[str(folder.path)] = design_id
            self.folders_file.parent.mkdir(parents=True, exist_ok=True)
            self.folders_file.write_text(json.dumps(self._folders, indent=2), encoding="utf-8")
            self.folders_changed.set()
        self.sync(design_id, design)
        return self.get(design_id), created

    def ensure(self, path: str | Path) -> DesignState:
        """The design for ``path``, opening the folder if it is not registered yet."""
        design_id = self.id_for(_absolute(path).path)
        return self.get(design_id) if design_id else self.open(path)[0]

    def where_used(self, design_id: str) -> list[str]:
        """Registered designs that use this one as a component, directly."""
        path = Path(self.get(design_id).path)
        out = []
        for folder, other in self.folders().items():
            try:
                if path in DesignFolder(Path(folder)).components():
                    out.append(other)
            except (OSError, ValueError):  # an unreadable design.json: its own build reports it
                continue
        return out

    def sync(self, design_id: str, design: DesignSpec | None = None) -> None:
        """Mirror the parts of design.json the panel shows (name, backend, brief) into the log."""
        state = self.get(design_id)
        try:
            design = design or self.folder(design_id).load()
        except (OSError, ValueError):  # unreadable design.json: the next build reports it
            return
        if (state.name, state.backend, state.brief) != (design.name, design.backend, design.brief):
            self.emit(design_id, DesignUpdated(name=design.name, backend=design.backend, brief=design.brief))
