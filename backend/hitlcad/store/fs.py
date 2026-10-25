"""Filesystem store: one directory per design, a JSONL event log, revision folders.

<root>/<design_id>/events.jsonl
<root>/<design_id>/revisions/<n>/...
<root>/<design_id>/unreadable-<time>/   a history another version of hitlcad wrote, set aside
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from hitlcad.core.events import EVENT_ADAPTER, Event
from hitlcad.store.base import UnreadableHistory

_DESIGN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class FileDesignStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._seq: dict[str, int] = {}

    def _dir(self, design_id: str) -> Path:
        if not _DESIGN_ID.match(design_id):
            raise ValueError(f"invalid design id {design_id!r}")
        return self.root / design_id

    def create(self, design_id: str) -> None:
        d = self._dir(design_id)
        d.mkdir(parents=True, exist_ok=False)
        (d / "events.jsonl").touch()

    def exists(self, design_id: str) -> bool:
        try:
            return (self._dir(design_id) / "events.jsonl").is_file()
        except ValueError:
            return False

    def append(self, design_id: str, event: Event) -> Event:
        path = self._dir(design_id) / "events.jsonl"
        if design_id not in self._seq:
            self._seq[design_id] = len(self._read_events(path))
        self._seq[design_id] += 1
        stored = event.model_copy(update={"seq": self._seq[design_id]})
        with path.open("a", encoding="utf-8") as f:
            f.write(stored.model_dump_json() + "\n")
        return stored

    def events(self, design_id: str, after: int = 0) -> list[Event]:
        try:
            events = self._read_events(self._dir(design_id) / "events.jsonl")
        except ValidationError as e:
            raise UnreadableHistory(f"design {design_id}'s history does not read: {e.error_count()} errors") from e
        return [e for e in events if e.seq > after]

    def set_aside(self, design_id: str) -> Path:
        d = self._dir(design_id)
        aside = d / f"unreadable-{datetime.now(UTC):%Y%m%d-%H%M%S}"
        aside.mkdir()
        for name in ("events.jsonl", "revisions"):
            if (d / name).exists():
                (d / name).rename(aside / name)
        (d / "events.jsonl").touch()
        self._seq.pop(design_id, None)
        return aside

    @staticmethod
    def _read_events(path: Path) -> list[Event]:
        with path.open(encoding="utf-8") as f:
            return [EVENT_ADAPTER.validate_json(line) for line in f if line.strip()]

    def revision_dir(self, design_id: str, revision_id: int) -> Path:
        d = self._dir(design_id) / "revisions" / str(revision_id)
        d.mkdir(parents=True, exist_ok=True)
        return d
