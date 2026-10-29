"""A design's files as one CRDT document: what hitlcad keeps as the design, of which its folder is the copy on disk.

design.json and the Python files are text; every other file of the folder is kept by its content (sha256). An edit
made on an earlier version is merged into the current one by tokens (`merge`): what others changed since stays, and
where both changed the same tokens, the edit merged now is kept and the place said. A `Snapshot` is the files at one
version, as builds and the panel read them.

Text positions in the document are UTF-8 byte offsets (pycrdt's), so edits found in Python strings are turned
into bytes here, and tree-sitter's are bytes already.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import tree_sitter_json
from diff_match_patch import diff_match_patch
from pycrdt import Doc, Map, Text
from tree_sitter import Language, Node, Parser

from hitlcad.core.design import DESIGN_FILE
from hitlcad.core.events import ChangedLine, CrossedEdit

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
    """A design's files at one version (0: as read from a folder, outside any document: `Scanned`)."""

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


class Scanned:
    """Design folders' files as they are on disk, outside any document (`hitlcad build`, with no server): the files a
    spec is made of (`DesignFolder.spec`), with the contents of those kept by their sums, for the build."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def __call__(self, folder: Path) -> Snapshot | None:
        """The files of a design folder; None when it holds no design."""
        disk = Disk.read(folder)
        self.blobs |= disk.blobs
        return Snapshot(0, disk.texts, disk.sums) if DESIGN_FILE in disk.texts else None


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
        return self.doc.get_update(since)

    def state(self) -> bytes:
        return self.doc.get_state()

    def apply(self, update: bytes) -> None:
        self.doc.apply_update(update)

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


# ---------------------------------------------------------------------- merging an edit made on an earlier version

#: A text's tokens: string literals, comments, numbers, words, runs of spaces, line breaks, any other character. A
#: merge never splits one, so a number two edits changed comes out as one of theirs, never digits of both.
_TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|#[^\n]*|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|\w+|[ \t]+|.',
                    re.DOTALL)  # fmt: skip


@dataclass(frozen=True)
class _Hunk:
    start: int  # tokens of the base it replaces: [start, end)
    end: int
    tokens: tuple[str, ...]
    mine: bool


def _hunks(base: list[str], new: list[str], mine: bool) -> list[_Hunk]:
    found = SequenceMatcher(None, base, new, autojunk=False).get_opcodes()
    return [_Hunk(i1, i2, tuple(new[j1:j2]), mine) for op, i1, i2, j1, j2 in found if op != "equal"]


def _applied(base: list[str], start: int, end: int, hunks: list[_Hunk]) -> list[str]:
    """base[start:end] with one side's hunks in it made."""
    out, at = [], start
    for h in hunks:
        out += [*base[at : h.start], *h.tokens]
        at = h.end
    return out + base[at:end]


def merge_text(base: str, mine: str, theirs: str) -> tuple[str, list[tuple[int, int]]]:
    """`mine` and `theirs`, both edits of `base`, as one text: what either changed alone is kept, the same change
    once, and insertions at one place both (theirs first). Where they changed the same tokens otherwise, `mine` (the
    edit merged now) is kept, and the place is said: its line in `base` and in the merged text."""
    b = _TOKEN.findall(base)
    hunks = sorted([*_hunks(b, _TOKEN.findall(theirs), False), *_hunks(b, _TOKEN.findall(mine), True)],
                   key=lambda h: (h.start, h.end, h.mine))  # fmt: skip
    groups: list[list[_Hunk]] = []
    for h in hunks:  # hunks that meet: they overlap, or insert at the same place
        last = groups[-1] if groups else None
        if last and (h.start < max(g.end for g in last) or h.start == h.end == last[0].start == last[0].end):
            last.append(h)
        else:
            groups.append([h])
    out: list[str] = []
    crossed: list[tuple[int, int]] = []
    at = 0
    for group in groups:
        start, end = group[0].start, max(h.end for h in group)
        ours, others = [h for h in group if h.mine], [h for h in group if not h.mine]
        out += b[at:start]
        made = _applied(b, start, end, ours), _applied(b, start, end, others)
        if not ours or not others or made[0] == made[1]:
            out += made[0] if ours else made[1]
        elif start == end:  # both inserted here
            out += [*made[1], *made[0]]
        else:
            crossed.append(("".join(b[:start]).count("\n") + 1, "".join(out).count("\n") + 1))
            out += made[0]
        at = end
    return "".join(out + b[at:]), crossed


def changed_lines(before: Snapshot, after: Snapshot, paths: Sequence[str]) -> list[ChangedLine]:
    """The lines of these text files that differ from `before` in `after`, as `after` has them (a removed one empty);
    files created or removed are said by their names."""
    out = []
    for path in paths:
        old, new = before.texts.get(path), after.texts.get(path)
        if old is None or new is None:
            continue
        a, b = old.splitlines(), new.splitlines()
        for op, _, _, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op != "equal":
                out += [ChangedLine(path=path, line=j + 1, now=b[j].strip()) for j in range(j1, j2)] or [
                    ChangedLine(path=path, line=j1 + 1, now="")]  # fmt: skip
    return out


def merge(base: Snapshot, mine: Snapshot, theirs: Snapshot) -> tuple[Snapshot, list[CrossedEdit]]:
    """The design's files with `mine` and `theirs`, two edits of `base`, both made (`merge_text` for each text file
    both changed); where they crossed, `mine` is kept and the place is said, for the human to look at."""
    texts: dict[str, str] = {}
    assets: dict[str, str] = {}
    crossed: list[CrossedEdit] = []
    for path in sorted({*base.texts, *mine.texts, *theirs.texts}):
        old, a, b = base.texts.get(path), mine.texts.get(path), theirs.texts.get(path)
        if a in (old, b):
            kept = b
        elif b == old:
            kept = a
        elif old is not None and a is not None and b is not None:
            kept, places = merge_text(old, a, b)
            lines = kept.splitlines()
            crossed += [CrossedEdit(path=path, line=line, now=lines[now - 1].strip() if now <= len(lines) else "")
                        for line, now in places]  # fmt: skip
        else:  # created on both sides, or removed on one and changed on the other
            kept = a
            crossed.append(CrossedEdit(path=path, line=1, now=(a or "").partition("\n")[0]))
        if kept is not None:
            texts[path] = kept
    for path in sorted({*base.assets, *mine.assets, *theirs.assets}):
        old, a, b = base.assets.get(path), mine.assets.get(path), theirs.assets.get(path)
        kept = b if a in (old, b) else a
        if a != old and b not in (old, a):
            crossed.append(CrossedEdit(path=path, line=0, now=""))
        if kept is not None:
            assets[path] = kept
    return Snapshot(theirs.version, texts, assets), crossed


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
