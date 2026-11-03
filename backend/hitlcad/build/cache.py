"""The part cache: each part file's outputs, by exactly the inputs it read.

A part file's outputs are reused when its own text, the `lib/` modules it imported, the values of the
parameters and interface values it read, and its backend's code and settings are all as they were: a design
copied or moved to another folder finds its parts too.
"""

from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hitlcad.backends.base import FileOutcome
from hitlcad.core.report import Reads
from hitlcad.kernel.cache import CacheDb


def link(source: Path | str, target: Path | str) -> None:
    """`target` as the same file as `source`, taking no more room (a hard link): the part files the cache and the
    builds share are written once and never again. On another drive than `source`, where no link reaches, a copy."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.copy2(source, target)


@dataclass(frozen=True)
class Inputs:
    """What any part file of one build may depend on besides its own recorded reads."""

    fingerprint: str  # the backend's code and settings
    sources: dict[str, str]
    assets: dict[str, str]  # vendor files, path -> sha256: any part file may load one
    values: dict[str, Any]
    interfaces: dict[str, Any]


class PartCache:
    """Each part file's outputs, an entry of the `parts` cache: its parts' files with its outcome. What a file reads
    is only known after it runs, so the `part reads` cache keeps the read-sets each file has had, by its path and
    text, and a lookup tries each against the current values."""

    def __init__(self, db: CacheDb) -> None:
        self.outputs = db.cache("parts")
        self.reads = db.cache("part reads")

    def key(self, file: str, declared: dict[str, list[str]], inputs: Inputs, reads: Reads) -> str | None:
        """The key of `file`'s outputs, asked by design.json for the parts and connectors `declared`, given what it
        read; None when something it read no longer exists."""
        if (any(n not in inputs.values for n in reads.parameters) or any(n not in inputs.interfaces for n in reads.interfaces)
                or any(m not in inputs.sources for m in reads.modules)):  # fmt: skip
            return None
        return self.outputs.key({"backend": inputs.fingerprint, "file": inputs.sources.get(file), "declared": declared,
                                 "modules": {m: inputs.sources[m] for m in reads.modules}, "assets": inputs.assets,
                                 "parameters": {n: inputs.values[n] for n in reads.parameters},
                                 "interfaces": {n: inputs.interfaces[n] for n in reads.interfaces}})  # fmt: skip

    def _seen(self, file: str, inputs: Inputs) -> str:
        return self.reads.key(file, inputs.sources.get(file))

    def lookup(self, file: str, declared: dict[str, list[str]], inputs: Inputs) -> tuple[Path, FileOutcome] | None:
        """Where `file`'s outputs are kept, and its outcome, when what it read is as it was."""
        for raw in self.reads.get(self._seen(file, inputs)) or []:
            key = self.key(file, declared, inputs, Reads.model_validate(raw))
            if key and (found := self.outputs.files(key)):
                return found[0], FileOutcome.model_validate(found[1])
        return None

    def store(self, outcome: FileOutcome, declared: dict[str, list[str]], work: Path, inputs: Inputs) -> None:
        key = self.key(outcome.file, declared, inputs, outcome.reads)
        if key is None or outcome.error:
            return
        staging = self.outputs.staging(key)
        for part in outcome.parts:
            for rel in part.files():
                link(work / rel, staging / rel)
        self.outputs.commit(key, staging, outcome.model_dump(mode="json"))
        seen = self._seen(outcome.file, inputs)
        reads = outcome.reads.model_dump(mode="json")
        if reads not in (known := self.reads.get(seen) or []):
            self.reads.put(seen, [*known, reads])

    @staticmethod
    def restore(kept: Path, outcome: FileOutcome, out: Path) -> None:
        for part in outcome.parts:
            for rel in part.files():
                link(kept / rel, out / rel)
