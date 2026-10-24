"""Persistence interface. Implementations must keep event logs append-only."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from hitlcad.core.events import Event


class DesignStore(Protocol):
    def create(self, design_id: str) -> None: ...

    def exists(self, design_id: str) -> bool: ...

    def append(self, design_id: str, event: Event) -> Event:
        """Persist ``event``, assigning the next sequence number, and return it."""
        ...

    def events(self, design_id: str, after: int = 0) -> list[Event]: ...

    def revision_dir(self, design_id: str, revision_id: int) -> Path:
        """Directory that holds the artifacts of one revision (created on demand)."""
        ...
