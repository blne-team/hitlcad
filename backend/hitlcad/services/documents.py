"""The designs' documents: each design's files as the database keeps them (`workspace.document`), every edit merged
into them, and their copy on disk.

Every writer edits the same way: on the version it started from (`base`), merged into the current one, so that
what others changed since stays. The panel's edits start from the revision on screen; a write to the folder (an
agent's tool, an editor, git) from the version that file was last written or read at: what its writer found on
disk (each file's own: a file not written over, because it changed on disk meanwhile, keeps the version it had).
Where two edits changed the same tokens, the edit merged now is kept and the place said (`Change.crossings`), for
the human to look at. The folder is then written with what changed, file by file, and
never over a file changed on disk since it was last read: that write is taken in next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hitlcad.core.events import ChangedLine, CrossedEdit
from hitlcad.kernel.errors import BASE_UNKNOWN
from hitlcad.store.base import DesignStore
from hitlcad.workspace.document import Disk, Document, Edit, Snapshot, changed_lines, merge, sha

#: A whole document is kept every this many versions, so that an earlier one replays from near it.
SNAPSHOT_EVERY = 100


@dataclass(frozen=True)
class Change:
    version: int
    since: int  # the version the edit was made on (the earliest, for files taken in from several)
    files: list[str]  # those whose content changed
    crossings: list[CrossedEdit]
    lines: list[ChangedLine]  # the lines of its text files it changed, as it left them


@dataclass(frozen=True)
class _OnDisk:
    """A file as it was last written or read: its sum, and the version of the design's files it was then."""

    sum: str
    version: int


@dataclass
class _Folder:
    """What hitlcad knows of a design's folder: each file as it was last written or read; each content a file had on
    disk, with the latest version it was (what an editor's buffer was read from); and the saves editors are about to
    make, by the version each was read from."""

    known: dict[str, _OnDisk] = field(default_factory=dict)
    was: dict[tuple[str, str], int] = field(default_factory=dict)  # (path, sum) -> version
    saving: dict[str, int] = field(default_factory=dict)  # path -> version

    def read(self, path: str, digest: str, version: int) -> None:
        self.known[path] = _OnDisk(digest, version)
        self.was[path, digest] = version


@dataclass
class _Held:
    doc: Document
    version: int
    files: Snapshot  # the document at `version`, read from any thread


class DocumentService:
    def __init__(self, store: DesignStore) -> None:
        self.store = store
        self._held: dict[str, _Held] = {}
        self._folders: dict[str, _Folder] = {}

    def _replay(self, design_id: str, version: int | None = None) -> tuple[int, Document]:
        """The document at `version` (its latest without), from the store; version 0: none."""
        version, snapshot, updates = self.store.document(design_id, version)
        doc = Document(snapshot)
        for update in updates:
            doc.apply(update)
        return version, doc

    def _get(self, design_id: str) -> _Held | None:
        held = self._held.get(design_id)
        if held is None:
            version, doc = self._replay(design_id)
            if not version:
                return None
            held = self._held[design_id] = _Held(doc, version, doc.snapshot(version))
        return held

    def has(self, design_id: str) -> bool:
        return self._get(design_id) is not None

    def current(self, design_id: str) -> Snapshot:
        held = self._get(design_id)
        assert held is not None, f"design {design_id} has no document"  # every design is created with one
        return held.files

    def files_at(self, design_id: str, version: int) -> Snapshot:
        """The design's files at `version`, read from any thread: a revision's, as it built them."""
        files = self.current(design_id)
        return files if version == files.version else self._replay(design_id, version)[1].snapshot(version)

    def create(self, design_id: str, folder: Path, disk: Disk, origin: str) -> Change:
        """The design's document, from its folder (or the files a new design starts with, which are written)."""
        doc = Document()
        doc.become(disk.texts, disk.sums)
        self.store.put_blobs(disk.blobs)
        whole = doc.update()
        version = self.store.add_update(design_id, origin, whole, snapshot=whole)
        held = self._held[design_id] = _Held(doc, version, doc.snapshot(version))
        self._folders[design_id] = _Folder()
        self.write(design_id, folder)
        return Change(version, 0, sorted([*held.files.texts, *held.files.assets]), [], [])

    def change(self, design_id: str, base: int, origin: str, edit: Edit, always: bool = False) -> Change | None:
        """Make `edit` on the design's files as they were at `base` and merge it into the current ones (`merge`);
        None when that changes nothing, unless `always` (an Undo is a step even when what it takes back is gone
        already). The folder is not written (`write`)."""
        held = self._get(design_id)
        assert held is not None
        if not 0 < base <= held.version:
            raise BASE_UNKNOWN(f"the design has versions 1 to {held.version}, not {base}")
        at_base, theirs = self.files_at(design_id, base), held.files
        start = Document()
        start.become(at_base.texts, at_base.assets)
        edit(start)
        merged, crossed = merge(at_base, start.snapshot(base), theirs)
        before = held.doc.state()
        held.doc.become(merged.texts, merged.assets)
        update = held.doc.update(before)
        if merged == theirs and not always:
            return None
        whole = held.doc.update() if (held.version + 1) % SNAPSHOT_EVERY == 0 else None
        held.version = self.store.add_update(design_id, origin, update, snapshot=whole)
        held.files = held.doc.snapshot(held.version)
        files = sorted(p for p in {*merged.texts, *theirs.texts, *merged.assets, *theirs.assets}
                       if (merged.texts.get(p), merged.assets.get(p)) != (theirs.texts.get(p), theirs.assets.get(p)))  # fmt: skip
        return Change(held.version, base, files, crossed, changed_lines(theirs, merged, files))

    def take_in(self, design_id: str, disk: Disk, origin: str) -> Change | None:
        """What the folder holds now, merged as `origin`'s edit: each file that changed on disk since it was last read
        or written, from that version, or from the one an editor saving it read it at (`saving`); files that started
        from different versions merge apart. None when nothing on disk changed. The folder is not written
        (`write`)."""
        on_disk, now = self._folders.setdefault(design_id, _Folder()), self.current(design_id)
        known = on_disk.known
        sums = {**{p: sha(t.encode()) for p, t in disk.texts.items()}, **disk.sums}
        held = {**{p: sha(t.encode()) for p, t in now.texts.items()}, **now.assets}
        written = {p for p in {*sums, *known, *held}  # changed on disk since, to other than the document holds
                   if sums.get(p) != (known[p].sum if p in known else held.get(p)) and sums.get(p) != held.get(p)}  # fmt: skip
        groups: dict[int, list[str]] = {}
        for path in sorted(written):
            version = on_disk.saving.pop(path, known[path].version if path in known else now.version)
            groups.setdefault(version, []).append(path)
            if path in sums:  # read now: the merge is written over it
                on_disk.read(path, sums[path], version)
            else:
                known.pop(path, None)
        self.store.put_blobs({disk.sums[p]: disk.blobs[disk.sums[p]] for p in written if p in disk.sums})

        def written_from(paths: list[str]) -> Edit:
            def edit(doc: Document) -> None:
                for path in paths:
                    doc.put(path, disk.texts.get(path), disk.sums.get(path))

            return edit

        changes = [c for v, paths in sorted(groups.items())
                   if (c := self.change(design_id, v, origin, written_from(paths)))]  # fmt: skip
        if not changes:
            return None
        return Change(changes[-1].version, min(c.since for c in changes), sorted({f for c in changes for f in c.files}),
                      [x for c in changes for x in c.crossings], [x for c in changes for x in c.lines])  # fmt: skip

    def saving(self, design_id: str, path: str, read: str) -> None:
        """An editor is about to save `path` from a buffer it read from disk when the file's sum was `read`: the save
        is taken in from the version the file had that content at, so what was written since stays. One that read
        content this server never saw is taken in as any other write."""
        on_disk = self._folders.setdefault(design_id, _Folder())
        if (version := on_disk.was.get((path, read))) is not None:
            on_disk.saving[path] = version

    def write(self, design_id: str, folder: Path) -> None:
        """Bring the folder to the design's files: each file that differs, atomically, unless it changed on disk since
        it was last read (taken in next, then written)."""
        files, on_disk = self.current(design_id), self._folders.setdefault(design_id, _Folder())
        known = on_disk.known
        for path, text in files.texts.items():
            self._put(on_disk, folder, path, text.encode(), files.version)
        for path, digest in files.assets.items():
            if path in known and known[path].sum == digest and (folder / path).is_file():
                continue
            if (data := self.store.blob(digest)) is not None:
                self._put(on_disk, folder, path, data, files.version)
        for path in [p for p in known if p not in files.texts and p not in files.assets]:
            target = folder / path
            if target.is_file() and sha(target.read_bytes()) == known[path].sum:
                target.unlink()
            if not target.is_file():
                del known[path]

    @staticmethod
    def _put(on_disk: _Folder, folder: Path, path: str, data: bytes, version: int) -> None:
        target, digest = folder / path, sha(data)
        now = sha(target.read_bytes()) if target.is_file() else None
        if path in on_disk.known and now not in (digest, on_disk.known[path].sum):
            return  # changed on disk since it was last read: taken in next
        if now != digest:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tmp")
            tmp.write_bytes(data)
            tmp.replace(target)  # atomic: readers never see half a file
        on_disk.read(path, digest, version)
