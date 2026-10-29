"""A design's files as one CRDT document: what hitlcad keeps as the design, of which its folder is the copy on disk.

design.json and the Python files are text that merges character by character (an edit made on an earlier
version keeps what others changed since); every other file of the folder is kept by its content (sha256), the
later of two writes winning. A `Snapshot` is the files at one version, as builds and the panel read them.

Text positions in the document are UTF-8 byte offsets (pycrdt's), so edits found in Python strings are turned
into bytes here, and tree-sitter's are bytes already.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tree_sitter_json
from diff_match_patch import diff_match_patch
from pycrdt import Doc, Map, Text
from tree_sitter import Language, Node, Parser

from hitlcad.core.design import DESIGN_FILE
from hitlcad.core.events import CrossedEdit

BUILD_DIR = "build"
CODE = ("interfaces.py", "assembly.py")
CODE_DIRS = ("parts", "lib")
ASSET_DIRS = ("vendor",)
#: Never part of a design: hitlcad's outputs, caches, version control.
IGNORED = frozenset({BUILD_DIR, f".{BUILD_DIR}.tmp", f".{BUILD_DIR}.old", "__pycache__", ".git"})
TEMPORARY = (".tmp", "~", ".swp")  # editors' and hitlcad's own files on the way to replacing another

_DIFF = diff_match_patch()
_DIFF.Diff_Timeout = 0  # exact diffs: design files are small, and a merge must not guess
_JSON = Parser(Language(tree_sitter_json.language()))


def is_text(path: str) -> bool:
    return path == DESIGN_FILE or path.endswith(".py")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def in_design(rel: Path) -> bool:
    """Whether a path inside a design folder is one of its files."""
    return not IGNORED & set(rel.parts) and not rel.name.endswith(TEMPORARY)


@dataclass(frozen=True)
class Snapshot:
    """A design's files at one version (0: as read from a folder, outside any document)."""

    version: int
    texts: dict[str, str]
    assets: dict[str, str] = field(default_factory=dict)  # relative path -> sha256

    @property
    def design(self) -> str | None:
        return self.texts.get(DESIGN_FILE)

    def sources(self) -> dict[str, str]:
        """The design's code, by path relative to the folder."""
        return {p: t for p, t in sorted(self.texts.items())
                if p in CODE or (p.split("/")[0] in CODE_DIRS and p.endswith(".py"))}  # fmt: skip

    def vendor(self) -> dict[str, str]:
        return {p: s for p, s in sorted(self.assets.items()) if p.split("/")[0] in ASSET_DIRS}

    @classmethod
    def scan(cls, folder: Path) -> Snapshot | None:
        """The files of a design folder on disk, as they are; None when it holds no design."""
        disk = Disk.read(folder)
        return cls(0, disk.texts, disk.sums) if DESIGN_FILE in disk.texts else None


@dataclass
class Disk:
    """What a design folder holds on disk: its text files, and every other file's content by its sum."""

    texts: dict[str, str]
    sums: dict[str, str]
    blobs: dict[str, bytes]  # sum -> content

    @classmethod
    def read(cls, folder: Path) -> Disk:
        texts, sums, blobs = {}, {}, {}
        for path in _files(folder):
            rel = path.relative_to(folder).as_posix()
            data = path.read_bytes()
            if is_text(rel):
                try:
                    texts[rel] = data.decode("utf-8")
                    continue
                except UnicodeDecodeError:  # kept by its content, as any other file is
                    pass
            sums[rel] = sha(data)
            blobs[sums[rel]] = data
        return cls(texts, sums, blobs)


def _files(folder: Path) -> Iterator[Path]:
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED)
        for name in sorted(names):
            if not name.endswith(TEMPORARY):
                yield Path(root) / name


class Document:
    """The CRDT document of one design's files."""

    def __init__(self, update: bytes | None = None) -> None:
        self.doc = Doc()
        self._texts = self.doc.get("files", type=Map)
        self._assets = self.doc.get("assets", type=Map)
        if update:
            self.doc.apply_update(update)

    def update(self, since: bytes | None = None) -> bytes:
        """Everything (or what changed since the state `since`) as one update."""
        return self.doc.get_update(since) if since is not None else self.doc.get_update()

    def state(self) -> bytes:
        return self.doc.get_state()

    def apply(self, update: bytes) -> None:
        self.doc.apply_update(update)

    def fork(self) -> Document:
        """A copy to edit apart (an editor of its own), whose changes merge back with `update(state)`."""
        return Document(self.update())

    def snapshot(self, version: int) -> Snapshot:
        return Snapshot(version, {p: str(t) for p, t in sorted(self._texts.items())},
                        {p: str(s) for p, s in sorted(self._assets.items())})  # fmt: skip

    def text(self, path: str) -> str | None:
        found = self._texts.get(path)
        return None if found is None else str(found)

    def write_text(self, path: str, new: str) -> None:
        """Make the file `new`, by the smallest edit of what it is: what it keeps stays the same characters, which
        others' edits made since merge around."""
        with self.doc.transaction():
            text = self._texts.get(path)
            if text is None:
                self._texts[path] = Text(new)
                return
            at = 0  # bytes
            for op, chunk in _DIFF.diff_main(str(text), new):
                size = len(chunk.encode())
                if op == _DIFF.DIFF_EQUAL:
                    at += size
                elif op == _DIFF.DIFF_DELETE:
                    del text[at : at + size]
                else:
                    text.insert(at, chunk)
                    at += size

    def write_asset(self, path: str, digest: str) -> None:
        if self._assets.get(path) != digest:
            self._assets[path] = digest

    def put(self, path: str, text: str | None, digest: str | None) -> None:
        """Make one file this text, or this content (its sum), or remove it (neither)."""
        with self.doc.transaction():
            if text is None and path in self._texts:
                del self._texts[path]
            if digest is None and path in self._assets:
                del self._assets[path]
            if text is not None:
                self.write_text(path, text)
            if digest is not None:
                self.write_asset(path, digest)

    def become(self, texts: dict[str, str], sums: dict[str, str]) -> None:
        """Make the files exactly these: as a folder holds them after someone wrote it."""
        with self.doc.transaction():
            for path in sorted({*self._texts.keys(), *self._assets.keys(), *texts, *sums}):
                self.put(path, texts.get(path), sums.get(path))


# ---------------------------------------------------------------------- edits that crossed


def crossings(base: Snapshot, mine: Snapshot, theirs: Snapshot, merged: Snapshot) -> list[CrossedEdit]:
    """Places both `mine` and `theirs` changed from `base` (touching counts): a merge kept both, by rule, and the
    human should look at what it made."""
    out = []
    for path in sorted({*mine.texts, *theirs.texts, *base.texts}):
        old, a, b = base.texts.get(path), mine.texts.get(path), theirs.texts.get(path)
        if a == old or b == old:
            continue
        if old is None or a is None or b is None:  # created or removed on both sides
            out.append(CrossedEdit(path=path, line=1, now=(merged.texts.get(path) or "").partition("\n")[0]))
            continue
        ours, others = _changed(old, a), _changed(old, b)
        for start, end in ours:
            if any(start <= e and s <= end for s, e in others):
                line = old.count("\n", 0, start) + 1
                lines = (merged.texts.get(path) or "").splitlines()
                out.append(CrossedEdit(path=path, line=line, now=lines[line - 1].strip() if line <= len(lines) else ""))
                break
    for path in sorted({*mine.assets, *theirs.assets, *base.assets}):
        old, a, b = base.assets.get(path), mine.assets.get(path), theirs.assets.get(path)
        if a != old and b != old and a != b:
            out.append(CrossedEdit(path=path, line=0, now=""))
    return out


def _changed(old: str, new: str) -> list[tuple[int, int]]:
    """The spans of `old` (characters) an edit to `new` deleted or inserted at."""
    spans, at = [], 0
    for op, chunk in _DIFF.diff_main(old, new):
        if op == _DIFF.DIFF_EQUAL:
            at += len(chunk)
        elif op == _DIFF.DIFF_DELETE:
            spans.append((at, at + len(chunk)))
            at += len(chunk)
        else:
            spans.append((at, at))
    return spans


# ---------------------------------------------------------------------- design.json, edited where a value is

#: A step into JSON: an object's key, an array's index, or the element of an array of objects whose `key` is `value`.
Step = str | int | tuple[str, Any]


def json_set(text: str, path: Sequence[Step], value: Any) -> str:
    """`text` with the value at `path` replaced by `value` (its key added to its object when it is not there), and
    every other character as it was. Raises ValueError when the text is not JSON or the path is not in it."""
    data = text.encode()
    tree = _JSON.parse(data)
    if tree.root_node.has_error or not tree.root_node.named_children:
        raise ValueError(f"{DESIGN_FILE} is not JSON")
    node = tree.root_node.named_children[0]
    new = json.dumps(value, ensure_ascii=False).encode()
    for k, step in enumerate(path):
        found = _step(node, step, data)
        if found is None:
            if k == len(path) - 1 and isinstance(step, str) and node.type == "object":
                return _insert(data, node, step, new).decode()
            raise ValueError(f"{DESIGN_FILE} has no {'.'.join(map(_shown, path[: k + 1]))}")
        node = found
    return (data[: node.start_byte] + new + data[node.end_byte :]).decode()


def _shown(step: Step) -> str:
    return f"[{step[0]}={step[1]!r}]" if isinstance(step, tuple) else str(step)


def _key(pair: Node, data: bytes) -> str:
    key = pair.child_by_field_name("key")
    return json.loads(data[key.start_byte : key.end_byte]) if key else ""


def _step(node: Node, step: Step, data: bytes) -> Node | None:
    match step:
        case str() if node.type == "object":
            return next((p.child_by_field_name("value") for p in _pairs(node) if _key(p, data) == step), None)
        case int() if node.type == "array":
            items = node.named_children
            return items[step] if 0 <= step < len(items) else None
        case (str() as key, wanted) if node.type == "array":
            for item in node.named_children:
                found = _step(item, key, data) if item.type == "object" else None
                if found is not None and json.loads(data[found.start_byte : found.end_byte]) == wanted:
                    return item
    return None


def _pairs(node: Node) -> list[Node]:
    return [c for c in node.named_children if c.type == "pair"]


def _insert(data: bytes, obj: Node, key: str, value: bytes) -> bytes:
    """A new last pair of `obj`, indented as its others are."""
    pair = json.dumps(key, ensure_ascii=False).encode() + b": " + value
    pairs = _pairs(obj)
    if not pairs:
        return data[: obj.start_byte + 1] + pair + data[obj.start_byte + 1 :]
    first, last = pairs[0], pairs[-1]
    line_start = data.rfind(b"\n", 0, first.start_byte) + 1
    indent = data[line_start : first.start_byte]
    gap = b"\n" + indent if indent.strip() == b"" and line_start > obj.start_byte else b" "
    return data[: last.end_byte] + b"," + gap + pair + data[last.end_byte :]


Edit = Callable[[Document], None]
