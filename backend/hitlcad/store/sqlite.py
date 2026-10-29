"""The store: one SQLite database for what hitlcad keeps on this machine, beside the revisions' files.

    <data_dir>/hitlcad.db                        folders, events, revisions, their results, the designs' documents
    <data_dir>/designs/<id>/revisions/<n>/       a revision's files, while it keeps them: parts, view, outputs, log
    <data_dir>/designs/<id>/unreadable-<time>/   a design's history that did not read, set aside

A design's files are its document (`workspace.document`): the updates that made it, one per version, with a
snapshot every `SNAPSHOT_EVERY` versions so that an earlier version replays from near it, and the content of its
other files by their sums. A design's events are its history, folded into its state; a revision's event lists
it, and the revision itself (what it built from, by reference to those documents, and its report) is read when
asked for. Its results are a table too, so what one revision failed is a query. A database of another layout (`SCHEMA`) is set aside whole when this one opens it, as
``hitlcad.<time>.db``; a design's history that does not read is set aside on its own.

One server keeps a data folder: it holds ``hitlcad.lock`` while it runs, and another refuses to start on it
(its state in memory would part from the first's).
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from pydantic import ValidationError

from hitlcad.core.events import EVENT_ADAPTER, Event, RevisionCreated
from hitlcad.core.report import Revision
from hitlcad.kernel.errors import STORE_IN_USE, STORE_UNREADABLE

#: The tables' layout and what their rows hold (the events' types): a database of another is set aside. Raise it
#: with every change to either.
SCHEMA = 4
DATABASE = "hitlcad.db"
LOCK = "hitlcad.lock"

TABLES = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE folders (path TEXT PRIMARY KEY, design TEXT NOT NULL UNIQUE);
CREATE TABLE designs (id TEXT PRIMARY KEY);
CREATE TABLE events (design TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, at TEXT NOT NULL,
                     body TEXT NOT NULL, PRIMARY KEY (design, seq));
CREATE TABLE revisions (design TEXT NOT NULL, id INTEGER NOT NULL, configuration TEXT, status TEXT NOT NULL,
                        body TEXT NOT NULL, PRIMARY KEY (design, id));
CREATE TABLE results (design TEXT NOT NULL, revision INTEGER NOT NULL, idx INTEGER NOT NULL, code TEXT NOT NULL,
                      analysis TEXT NOT NULL, subject TEXT NOT NULL, status TEXT NOT NULL, severity TEXT NOT NULL,
                      file TEXT, line INTEGER, PRIMARY KEY (design, revision, idx));
CREATE INDEX results_by_code ON results (design, code, subject);
CREATE TABLE doc_updates (design TEXT NOT NULL, version INTEGER NOT NULL, origin TEXT NOT NULL, at TEXT NOT NULL,
                          body BLOB NOT NULL, PRIMARY KEY (design, version));
CREATE TABLE doc_snapshots (design TEXT NOT NULL, version INTEGER NOT NULL, body BLOB NOT NULL,
                            PRIMARY KEY (design, version));
CREATE TABLE blobs (sum TEXT PRIMARY KEY, body BLOB NOT NULL);
"""


def _stamp() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}"


class SqliteStore:
    def __init__(self, data_dir: Path) -> None:
        self.designs_dir = data_dir / "designs"
        self.designs_dir.mkdir(parents=True, exist_ok=True)
        self._held = FileLock(data_dir / LOCK)
        try:
            self._held.acquire(timeout=0)
        except Timeout:
            raise STORE_IN_USE(f"another hitlcad server keeps {data_dir}") from None
        path = data_dir / DATABASE
        self.set_aside_folders = self._set_aside_another_layout(path)
        new = not path.exists()
        # The server's event loop and its worker threads share one connection, one statement at a time.
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        self._db.execute("PRAGMA journal_mode=WAL")
        if new:  # the script is one transaction of its own
            meta = f"INSERT INTO meta VALUES ('schema', '{SCHEMA}');"
            self._db.executescript(f"BEGIN; {TABLES} {meta} COMMIT;")

    @staticmethod
    def _set_aside_another_layout(path: Path) -> frozenset[str]:
        """Move a database of another layout out of the way; the folders it had histories of."""
        if not path.exists():
            return frozenset()
        try:
            with closing(sqlite3.connect(path)) as db:  # closed before it is moved: Windows moves no open file
                meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
                folders = frozenset(p for (p,) in db.execute("SELECT path FROM folders"))
        except sqlite3.Error:
            meta, folders = {}, frozenset()
        if meta.get("schema") == str(SCHEMA):
            return frozenset()
        for suffix in ("", "-wal", "-shm"):  # a database is its file and its write-ahead log
            if (part := path.with_name(path.name + suffix)).exists():
                part.rename(path.with_name(f"{path.stem}.{_stamp()}{path.suffix}{suffix}"))
        return folders

    def close(self) -> None:
        with self._lock:
            self._db.close()
        self._held.release()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _query(self, sql: str, *args: Any) -> list[Any]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ------------------------------------------------------------------ folders and designs

    def folders(self) -> dict[str, str]:
        return dict(self._query("SELECT path, design FROM folders"))

    def bind(self, path: str, design_id: str) -> None:
        with self._transaction() as db:
            db.execute("INSERT OR REPLACE INTO folders VALUES (?, ?)", (path, design_id))

    def create(self, design_id: str) -> None:
        with self._transaction() as db:
            db.execute("INSERT INTO designs VALUES (?)", (design_id,))

    def exists(self, design_id: str) -> bool:
        return bool(self._query("SELECT 1 FROM designs WHERE id = ?", design_id))

    # ------------------------------------------------------------------ events and revisions

    @staticmethod
    def _insert(db: sqlite3.Connection, design_id: str, event: Event) -> Event:
        (last,) = db.execute("SELECT coalesce(max(seq), 0) FROM events WHERE design = ?", (design_id,)).fetchone()
        stored = event.model_copy(update={"seq": last + 1})
        db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                   (design_id, stored.seq, stored.type, stored.ts.isoformat(), stored.model_dump_json()))  # fmt: skip
        return stored

    def append(self, design_id: str, event: Event) -> Event:
        with self._transaction() as db:
            return self._insert(db, design_id, event)

    def add_revision(self, design_id: str, revision: Revision) -> Event:
        with self._transaction() as db:
            db.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?)", (design_id, revision.id, revision.configuration,
                       revision.report.status.value, revision.model_dump_json(exclude={"spec"})))  # fmt: skip
            db.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                (design_id, revision.id, k, r.code, r.analysis, r.subject, r.status, r.severity,
                 r.where.file if r.where else None, r.where.line if r.where else None)
                for k, r in enumerate(revision.report.results)
            ])  # fmt: skip
            return self._insert(db, design_id, RevisionCreated(revision=revision.info()))

    def events(self, design_id: str, after: int = 0) -> list[Event]:
        rows = self._query("SELECT body FROM events WHERE design = ? AND seq > ? ORDER BY seq", design_id, after)
        try:
            return [EVENT_ADAPTER.validate_json(body) for (body,) in rows]
        except ValidationError as e:
            raise STORE_UNREADABLE(f"design {design_id}'s history does not read: {e.error_count()} errors") from e

    def revision(self, design_id: str, revision_id: int) -> Revision | None:
        rows = self._query("SELECT body FROM revisions WHERE design = ? AND id = ?", design_id, revision_id)
        if not rows:
            return None
        try:
            return Revision.model_validate_json(rows[0][0])
        except ValidationError as e:
            raise STORE_UNREADABLE(f"design {design_id}'s revision {revision_id} does not read") from e

    def failing(self, design_id: str, revision_id: int) -> frozenset[tuple[str, str]]:
        rows = self._query("SELECT code, subject FROM results WHERE design = ? AND revision = ? AND status != 'pass'",
                           design_id, revision_id)  # fmt: skip
        return frozenset((code, subject) for code, subject in rows)

    def set_aside(self, design_id: str) -> Path:
        aside = self._dir(design_id) / f"unreadable-{_stamp()}"
        aside.mkdir(parents=True)
        with self._transaction() as db:
            for table in ("events", "revisions"):
                order = "seq" if table == "events" else "id"
                bodies = [b for (b,) in db.execute(f"SELECT body FROM {table} WHERE design = ? ORDER BY {order}",
                                                   (design_id,))]  # fmt: skip
                (aside / f"{table}.jsonl").write_text("".join(f"{b}\n" for b in bodies), encoding="utf-8")
            for table in ("events", "revisions", "results"):
                db.execute(f"DELETE FROM {table} WHERE design = ?", (design_id,))
        if (revisions := self._dir(design_id) / "revisions").exists():
            shutil.move(revisions, aside / "revisions")
        return aside

    # ------------------------------------------------------------------ documents

    def add_update(self, design_id: str, origin: str, update: bytes, snapshot: bytes | None = None) -> int:
        with self._transaction() as db:
            (last,) = db.execute("SELECT coalesce(max(version), 0) FROM doc_updates WHERE design = ?",
                                 (design_id,)).fetchone()  # fmt: skip
            db.execute("INSERT INTO doc_updates VALUES (?, ?, ?, ?, ?)",
                       (design_id, last + 1, origin, datetime.now(UTC).isoformat(), update))  # fmt: skip
            if snapshot is not None:
                db.execute("INSERT INTO doc_snapshots VALUES (?, ?, ?)", (design_id, last + 1, snapshot))
            return int(last + 1)

    def document(self, design_id: str, version: int | None = None) -> tuple[int, bytes | None, list[bytes]]:
        rows = self._query("SELECT version, body FROM doc_snapshots WHERE design = ? AND version <= ? "
                           "ORDER BY version DESC LIMIT 1", design_id, 2**62 if version is None else version)  # fmt: skip
        start, snapshot = rows[0] if rows else (0, None)
        updates = self._query("SELECT version, body FROM doc_updates WHERE design = ? AND version > ? AND version <= ? "
                              "ORDER BY version", design_id, start, 2**62 if version is None else version)  # fmt: skip
        return (updates[-1][0] if updates else start), snapshot, [u for _, u in updates]

    def put_blobs(self, blobs: dict[str, bytes]) -> None:
        with self._transaction() as db:
            db.executemany("INSERT OR IGNORE INTO blobs VALUES (?, ?)", list(blobs.items()))

    def blob(self, digest: str) -> bytes | None:
        rows = self._query("SELECT body FROM blobs WHERE sum = ?", digest)
        return rows[0][0] if rows else None

    # ------------------------------------------------------------------ files

    def _dir(self, design_id: str) -> Path:
        if not self.exists(design_id):  # ids come from requests too: never a path elsewhere
            raise ValueError(f"no design {design_id!r}")
        return self.designs_dir / design_id

    def revision_dir(self, design_id: str, revision_id: int) -> Path:
        return self._dir(design_id) / "revisions" / str(revision_id)
