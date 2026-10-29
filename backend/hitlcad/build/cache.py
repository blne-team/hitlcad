"""The part cache: each part file's outputs, by exactly the inputs it read.

A part file's outputs are reused when its own text, the `lib/` modules it imported, the values of the
parameters and interface values it read, and its backend's code and settings are all as they were.
What a file reads is only known after it runs, so the cache remembers the read-sets each file has
had, by its path and text, and tries each against the current values: a design copied or moved to
another folder finds its parts too.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hitlcad.backends.base import FileOutcome
from hitlcad.core.report import Reads

OUTCOME = "outcome.json"


def link(source: Path, target: Path) -> None:
    """`target` as the same file as `source`, taking no more room (a hard link): the part files the cache and the
    builds share are written once and never again."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    os.link(source, target)


@dataclass(frozen=True)
class Inputs:
    """What any part file of one build may depend on besides its own recorded reads."""

    fingerprint: str  # the backend's code and settings
    sources: dict[str, str]
    assets: dict[str, str]  # vendor files, path -> sha256: any part file may load one
    values: dict[str, Any]
    interfaces: dict[str, Any]


class PartCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _index(self, file: str, inputs: Inputs) -> Path:
        digest = hashlib.sha256(json.dumps([file, inputs.sources.get(file)]).encode()).hexdigest()
        return self.root / "index" / f"{digest}.json"

    @staticmethod
    def key(file: str, declared: dict[str, list[str]], inputs: Inputs, reads: Reads) -> str | None:
        """The cache key of `file`, asked by design.json for the parts and connectors `declared`, given what it
        read; None when something it read no longer exists."""
        if (any(n not in inputs.values for n in reads.parameters) or any(n not in inputs.interfaces for n in reads.interfaces)
                or any(m not in inputs.sources for m in reads.modules)):  # fmt: skip
            return None
        data = {"backend": inputs.fingerprint, "file": inputs.sources.get(file), "declared": declared,
                "modules": {m: inputs.sources[m] for m in reads.modules}, "assets": inputs.assets,
                "parameters": {n: inputs.values[n] for n in reads.parameters},
                "interfaces": {n: inputs.interfaces[n] for n in reads.interfaces}}  # fmt: skip
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    def lookup(self, file: str, declared: dict[str, list[str]], inputs: Inputs) -> tuple[str, FileOutcome] | None:
        index = self._index(file, inputs)
        if not index.is_file():
            return None
        for raw in json.loads(index.read_text(encoding="utf-8")):
            key = self.key(file, declared, inputs, Reads.model_validate(raw))
            if key and (self.root / key / OUTCOME).is_file():
                return key, FileOutcome.model_validate_json((self.root / key / OUTCOME).read_text(encoding="utf-8"))
        return None

    def store(self, outcome: FileOutcome, declared: dict[str, list[str]], work: Path, inputs: Inputs) -> None:
        key = self.key(outcome.file, declared, inputs, outcome.reads)
        if key is None or outcome.error:
            return
        target = self.root / key
        staging = target.with_name(f".{key}.tmp")
        shutil.rmtree(staging, ignore_errors=True)
        for part in outcome.parts:
            for rel in part.files():
                link(work / rel, staging / rel)
        staging.mkdir(parents=True, exist_ok=True)
        (staging / OUTCOME).write_text(outcome.model_dump_json(), encoding="utf-8")
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
        index = self._index(outcome.file, inputs)
        seen = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else []
        reads = outcome.reads.model_dump()
        if reads not in seen:
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(json.dumps([*seen, reads]), encoding="utf-8")

    def restore(self, key: str, outcome: FileOutcome, out: Path) -> None:
        for part in outcome.parts:
            for rel in part.files():
                link(self.root / key / rel, out / rel)
