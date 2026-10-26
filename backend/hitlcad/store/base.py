"""Persistence interface. Implementations keep event logs append-only."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from hitlcad.core.events import Event
from hitlcad.core.report import Revision


class DesignStore(Protocol):
    #: Folders that had a history in a database of another layout, set aside when this store opened.
    set_aside_folders: frozenset[str]

    def folders(self) -> dict[str, str]:
        """Every registered folder: path -> design id."""
        ...

    def bind(self, path: str, design_id: str) -> None: ...

    def create(self, design_id: str) -> None: ...

    def exists(self, design_id: str) -> bool: ...

    def append(self, design_id: str, event: Event) -> Event:
        """Persist ``event``, assigning the next sequence number, and return it."""
        ...

    def add_revision(self, design_id: str, revision: Revision) -> Event:
        """Persist a revision and its results with the event that lists it, at once; return the event."""
        ...

    def events(self, design_id: str, after: int = 0) -> list[Event]:
        """The design's events after ``after``; raises `STORE_UNREADABLE` when they do not read."""
        ...

    def revision(self, design_id: str, revision_id: int) -> Revision | None:
        """A revision in full; raises `STORE_UNREADABLE` when it does not read."""
        ...

    def failing(self, design_id: str, revision_id: int) -> frozenset[tuple[str, str]]:
        """(code, subject) of each result of a revision that does not pass."""
        ...

    def set_aside(self, design_id: str) -> Path:
        """Move the design's history (its events and revisions) out of the way, intact, and start it a new,
        empty one. Returns where it went."""
        ...

    def revision_dir(self, design_id: str, revision_id: int) -> Path:
        """Directory that holds the files of one revision (created on demand)."""
        ...

    def close(self) -> None: ...
