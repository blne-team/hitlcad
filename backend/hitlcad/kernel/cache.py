"""Caches: what is costly to compute or load, kept by the content of everything it depends on, so an entry is never
stale, only unused, and the least recently used is let go first.

- `MemoryLru` (in `execution.runtime`, which build workers run in any Python): live objects in this process (part
  shapes, loaded revisions, a worker's exports), up to a capacity measured as each cache measures its values.
- `CacheDb`: a data folder's cache on disk, one SQLite file of entries by cache name and key under one budget. An
  entry is a value (bytes), a directory of files (what must be a file: part files hard-linked into builds, scenes
  the server sends), or both.
- `Cache`: one named cache of a `CacheDb`: its keys (the hash of its name, the version of the code that computes its
  entries and what each depends on), JSON values read through a memory tier, and directories.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hitlcad.kernel.lru import MemoryLru

#: How long a connection waits for another thread's write, seconds.
BUSY_S = 60.0
#: Once over budget, entries are let go until the cache holds this share of it, so not every write evicts.
EVICT_TO = 0.9


class CacheDb:
    """A data folder's cache: `cache.sqlite` in `root`, entries by cache name and key, each a value, a directory of
    files under `root/files/<name>/<key>`, or both; the least recently used let go first once they hold more than
    `budget` bytes."""

    def __init__(self, root: Path, budget: int) -> None:
        self.root = root
        self.budget = budget
        root.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS entries (name TEXT NOT NULL, key TEXT NOT NULL, value BLOB, "
                       "files INTEGER NOT NULL, bytes INTEGER NOT NULL, used REAL NOT NULL, PRIMARY KEY (name, key))")  # fmt: skip
            db.execute("CREATE INDEX IF NOT EXISTS entries_used ON entries (used)")

    def _db(self) -> sqlite3.Connection:
        """This thread's connection (a connection is one thread's)."""
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.root / "cache.sqlite", timeout=BUSY_S, isolation_level=None)
            db.execute("PRAGMA synchronous=NORMAL")
            self._local.db = db
        return db

    def cache(self, name: str, version: str = "", memory: int = 0) -> Cache:
        """The named cache: its entries' keys include `version` (the code that computes them), and up to `memory`
        bytes of its values are kept decoded in this process."""
        return Cache(self, name, version, memory)

    def directory(self, name: str, key: str) -> Path:
        return self.root / "files" / name / key

    def read(self, name: str, key: str) -> tuple[bytes | None, bool] | None:
        """An entry's value and whether it has files, marked used; None where there is none."""
        db = self._db()
        row = db.execute("SELECT value, files FROM entries WHERE name = ? AND key = ?", (name, key)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE entries SET used = ? WHERE name = ? AND key = ?", (time.time(), name, key))
        return row[0], bool(row[1])

    def write(self, name: str, key: str, value: bytes | None, files: int | None) -> None:
        """An entry: its value, and the bytes of its directory where it has one (already in place)."""
        size = (len(value) if value is not None else 0) + (files or 0)
        self._db().execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?, ?)",
                           (name, key, value, files is not None, size, time.time()))  # fmt: skip
        self.evict()

    def evict(self) -> None:
        """Let the least recently used go while the entries hold more than the budget."""
        db = self._db()
        (held,) = db.execute("SELECT COALESCE(SUM(bytes), 0) FROM entries").fetchone()
        if held <= self.budget:
            return
        target = self.budget * EVICT_TO
        for name, key, files, size in db.execute(
            "SELECT name, key, files, bytes FROM entries ORDER BY used"
        ).fetchall():
            if held <= target:
                break
            db.execute("DELETE FROM entries WHERE name = ? AND key = ?", (name, key))
            if files:  # what a build linked from it keeps its data: a hard link is the file
                shutil.rmtree(self.directory(name, key), ignore_errors=True)
            held -= size


class Cache:
    """One named cache of a `CacheDb` (`CacheDb.cache`)."""

    def __init__(self, db: CacheDb, name: str, version: str, memory: int) -> None:
        self.db = db
        self.name = name
        self.version = version
        self._memory: MemoryLru[str, tuple[Any, int]] = MemoryLru(
            memory, size=lambda kept: kept[1]
        )  # decoded, its bytes

    def key(self, *parts: Any) -> str:
        """An entry's key: the hash of this cache's name and version and of what the entry depends on."""
        text = json.dumps([self.name, self.version, *parts], sort_keys=True, default=str)
        return hashlib.sha256(text.encode()).hexdigest()

    # ------------------------------------------------------------------ values

    def get(self, key: str) -> Any | None:
        """A JSON value, decoded; None where there is none. Shared with every reader: never change it."""
        kept = self._memory.get(key)
        if kept is not None:
            return kept[0]
        found = self.db.read(self.name, key)
        if found is None or found[0] is None:
            return None
        value = json.loads(found[0])
        self._memory.put(key, (value, len(found[0])))
        return value

    def put(self, key: str, value: Any) -> Any:
        data = json.dumps(value, separators=(",", ":")).encode()
        self.db.write(self.name, key, data, None)
        self._memory.put(key, (value, len(data)))
        return value

    def get_or(self, key: str, compute: Callable[[], Any]) -> Any:
        """A JSON value, computed and kept where there is none."""
        found = self.get(key)
        return found if found is not None else self.put(key, compute())

    # ------------------------------------------------------------------ directories

    def files(self, key: str) -> tuple[Path, Any] | None:
        """An entry's directory and its value (None without one), marked used; None where there is none."""
        found = self.db.read(self.name, key)
        directory = self.db.directory(self.name, key)
        if found is None or not found[1] or not directory.is_dir():
            return None
        return directory, json.loads(found[0]) if found[0] is not None else None

    def staging(self, key: str) -> Path:
        """A new, empty directory to fill for `commit`, beside where the entry's will be."""
        parent = self.db.directory(self.name, key).parent
        parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f".{key[:16]}.", dir=parent))

    def commit(self, key: str, staging: Path, value: Any = None) -> Path:
        """A filled staging directory as the entry's, whole or not at all, with `value`. Where another commit of
        the same key came first, its directory stays (equal keys, equal contents: a directory being read is never
        replaced) and this one is let go."""
        directory = self.db.directory(self.name, key)
        try:
            staging.rename(directory)
        except OSError:
            if not directory.is_dir():
                raise
            shutil.rmtree(staging, ignore_errors=True)
        size = sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())
        data = json.dumps(value, separators=(",", ":")).encode() if value is not None else None
        self.db.write(self.name, key, data, size)
        return directory
