"""The designs' documents: each design's files as the database keeps them (`workspace.document`), every edit merged
into them, and their copy on disk.

Every writer edits the same way: on the version it started from (`base`), merged into the current one, so that
what others changed since stays. The panel's edits start from the revision on screen; an agent's file writes from
the version its tool call started on, which its hooks say; any other write to a file from the version that file was
last written or read at (each file's own: a file not written over, because it changed on disk meanwhile, keeps the
version it had). Where two edits made on the same version changed the same text, the merge keeps both by rule and
says so (`Change.crossings`), for the human to look at. The folder is then written with what changed, file by file,
and never over a file changed on disk since it was last read: that write is taken in next.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hitlcad.core.events import CrossedEdit
from hitlcad.kernel.errors import BASE_UNKNOWN
from hitlcad.store.base import DesignStore
from hitlcad.workspace.document import Disk, Document, Edit, Snapshot, crossings, sha

#: A whole document is kept every this many versions, so that an earlier one replays from near it.
SNAPSHOT_EVERY = 100


@dataclass(frozen=True)
class Change:
    version: int
    since: int  # the version the edit was made on (the earliest, for files taken in from several)
    files: list[str]  # those whose content changed
    crossings: list[CrossedEdit]


@dataclass(frozen=True)
class _OnDisk:
    """A file as it was last written or read: its sum, and the version of the design's files it was then."""

    sum: str
    version: int


@dataclass
class _Held:
    doc: Document
    version: int
    files: Snapshot  # the document at `version`, read from any thread


class DocumentService:
    def __init__(self, store: DesignStore) -> None:
        self.store = store
        self._held: dict[str, _Held] = {}
        self._disk: dict[str, dict[str, _OnDisk]] = {}  # design -> path -> the file as last read or written

    def _get(self, design_id: str) -> _Held | None:
        held = self._held.get(design_id)
        if held is None:
            version, snapshot, updates = self.store.document(design_id)
            if not version:
                return None
            doc = Document(snapshot)
            for update in updates:
                doc.apply(update)
            held = self._held[design_id] = _Held(doc, version, doc.snapshot(version))
        return held

    def has(self, design_id: str) -> bool:
        return self._get(design_id) is not None

    def current(self, design_id: str) -> Snapshot:
        held = self._get(design_id)
        assert held is not None, f"design {design_id} has no document"  # every design is created with one
        return held.files

    def at(self, design_id: str, version: int) -> Document:
        """The document as it was at `version`, to edit apart."""
        held = self._get(design_id)
        assert held is not None
        if not 0 < version <= held.version:
            raise BASE_UNKNOWN(f"the design has versions 1 to {held.version}, not {version}")
        if version == held.version:
            return held.doc.fork()
        _, snapshot, updates = self.store.document(design_id, version)
        doc = Document(snapshot)
        for update in updates:
            doc.apply(update)
        return doc

    def create(self, design_id: str, folder: Path, disk: Disk, origin: str) -> Change:
        """The design's document, from its folder (or the files a new design starts with, which are written)."""
        doc = Document()
        doc.become(disk.texts, disk.sums)
        self.store.put_blobs(disk.blobs)
        whole = doc.update()
        version = self.store.add_update(design_id, origin, whole, snapshot=whole)
        held = self._held[design_id] = _Held(doc, version, doc.snapshot(version))
        self._disk[design_id] = {}
        self._write(design_id, folder, held.files)
        return Change(version, 0, sorted([*held.files.texts, *held.files.assets]), [])

    def change(self, design_id: str, folder: Path, base: int, origin: str, edit: Edit) -> Change | None:
        """Make `edit` on the document as it was at `base` and merge it into the current one; None when that
        changes nothing."""
        held = self._get(design_id)
        assert held is not None
        start = self.at(design_id, base)
        before, at_base = start.state(), start.snapshot(base)
        edit(start)
        update = start.update(before)
        theirs = held.files
        held.doc.apply(update)
        merged = held.doc.snapshot(held.version)
        if merged == theirs:
            return None
        whole = held.doc.update() if (held.version + 1) % SNAPSHOT_EVERY == 0 else None
        held.version = self.store.add_update(design_id, origin, update, snapshot=whole)
        held.files = held.doc.snapshot(held.version)
        files = sorted(p for p in {*merged.texts, *theirs.texts, *merged.assets, *theirs.assets}
                       if (merged.texts.get(p), merged.assets.get(p)) != (theirs.texts.get(p), theirs.assets.get(p)))  # fmt: skip
        crossed = crossings(at_base, start.snapshot(0), theirs, merged) if base != theirs.version else []
        self._write(design_id, folder, held.files)
        return Change(held.version, base, files, crossed)

    def take_in(self, design_id: str, folder: Path, disk: Disk, base: int | None, origin: str) -> Change | None:
        """What the folder holds now, written by someone starting from version `base`, merged as their edit: each
        file that changed on disk since it was last read or written, from that version when `base` is not known
        (files that started from different versions merge apart). None when nothing on disk changed."""
        known, now = self._disk.setdefault(design_id, {}), self.current(design_id)
        sums = {**{p: sha(t.encode()) for p, t in disk.texts.items()}, **disk.sums}
        held = {**{p: sha(t.encode()) for p, t in now.texts.items()}, **now.assets}
        written = {p for p in {*sums, *known, *held}  # changed on disk since, to other than the document holds
                   if sums.get(p) != (known[p].sum if p in known else held.get(p)) and sums.get(p) != held.get(p)}  # fmt: skip
        groups: dict[int, list[str]] = {}
        for path in sorted(written):
            version = base or (known[path].version if path in known else now.version)
            groups.setdefault(version, []).append(path)
            if path in sums:  # read now: the merge is written over it
                known[path] = _OnDisk(sums[path], version)
            else:
                known.pop(path, None)
        self.store.put_blobs(disk.blobs)

        def written_from(paths: list[str]) -> Edit:
            def edit(doc: Document) -> None:
                for path in paths:
                    doc.put(path, disk.texts.get(path), disk.sums.get(path))

            return edit

        changes = [c for v, paths in sorted(groups.items())
                   if (c := self.change(design_id, folder, v, origin, written_from(paths)))]  # fmt: skip
        self._write(design_id, folder, self.current(design_id))  # a folder behind the document too
        if not changes:
            return None
        return Change(changes[-1].version, min(c.since for c in changes), sorted({f for c in changes for f in c.files}),
                      [x for c in changes for x in c.crossings])  # fmt: skip

    def _write(self, design_id: str, folder: Path, files: Snapshot) -> list[str]:
        """Bring the folder to `files`: each file that differs, atomically, unless it changed on disk since it was
        last read (taken in next, then written)."""
        known = self._disk.setdefault(design_id, {})
        written = []
        for path, text in files.texts.items():
            if self._put(folder, path, text.encode(), known, files.version):
                written.append(path)
        for path, digest in files.assets.items():
            if path in known and known[path].sum == digest and (folder / path).is_file():
                continue
            data = self.store.blob(digest)
            if data is not None and self._put(folder, path, data, known, files.version):
                written.append(path)
        for path in [p for p in known if p not in files.texts and p not in files.assets]:
            target = folder / path
            if target.is_file() and sha(target.read_bytes()) == known[path].sum:
                target.unlink()
                written.append(path)
            if not target.is_file():
                del known[path]
        return written

    @staticmethod
    def _put(folder: Path, path: str, data: bytes, known: dict[str, _OnDisk], version: int) -> bool:
        target, digest = folder / path, sha(data)
        on_disk = sha(target.read_bytes()) if target.is_file() else None
        if on_disk == digest:
            known[path] = _OnDisk(digest, version)
            return False
        if path in known and on_disk != known[path].sum:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(target)  # atomic: readers never see half a file
        known[path] = _OnDisk(digest, version)
        return True
