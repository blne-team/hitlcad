"""The part cache: each part file's outputs, by exactly the inputs it read.

A part file's outputs are reused when its own text, the `lib/` modules it imported, the values of the
parameters and interface values it read, and its backend's code and settings are all as they were.
What a file reads is only known after it runs, so the cache remembers the read-sets each file has
had and tries each against the current values.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from hitlcad.backends.base import FileOutcome
from hitlcad.core.report import Reads

OUTCOME = "outcome.json"


class PartCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _index(self, scope: str, file: str) -> Path:
        digest = hashlib.sha256(json.dumps([scope, file]).encode()).hexdigest()
        return self.root / "index" / f"{digest}.json"

    @staticmethod
    def key(fingerprint: str, file: str, sources: dict[str, str], values: dict[str, Any], interfaces: dict[str, Any],
            reads: Reads) -> str | None:  # fmt: skip
        """The cache key of `file` given what it read, or None when something it read no longer exists."""
        if (any(n not in values for n in reads.parameters) or any(n not in interfaces for n in reads.interfaces)
                or any(m not in sources for m in reads.modules)):  # fmt: skip
            return None
        data = {"backend": fingerprint, "file": sources.get(file), "modules": {m: sources[m] for m in reads.modules},
                "parameters": {n: values[n] for n in reads.parameters},
                "interfaces": {n: interfaces[n] for n in reads.interfaces}}  # fmt: skip
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    def lookup(self, scope: str, fingerprint: str, file: str, sources: dict[str, str], values: dict[str, Any],
               interfaces: dict[str, Any]) -> tuple[str, FileOutcome] | None:  # fmt: skip
        index = self._index(scope, file)
        if not index.is_file():
            return None
        for raw in json.loads(index.read_text()):
            key = self.key(fingerprint, file, sources, values, interfaces, Reads.model_validate(raw))
            if key and (self.root / key / OUTCOME).is_file():
                return key, FileOutcome.model_validate_json((self.root / key / OUTCOME).read_text())
        return None

    def store(self, scope: str, fingerprint: str, outcome: FileOutcome, work: Path, sources: dict[str, str],
              values: dict[str, Any], interfaces: dict[str, Any]) -> None:  # fmt: skip
        key = self.key(fingerprint, outcome.file, sources, values, interfaces, outcome.reads)
        if key is None or outcome.error:
            return
        target = self.root / key
        staging = target.with_name(f".{key}.tmp")
        shutil.rmtree(staging, ignore_errors=True)
        for part in outcome.parts:
            for rel in filter(None, (part.mesh, part.step, part.objects)):
                (staging / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(work / rel, staging / rel)
        staging.mkdir(parents=True, exist_ok=True)
        (staging / OUTCOME).write_text(outcome.model_dump_json())
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
        index = self._index(scope, outcome.file)
        seen = json.loads(index.read_text()) if index.is_file() else []
        reads = outcome.reads.model_dump()
        if reads not in seen:
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(json.dumps([*seen, reads]))

    def restore(self, key: str, outcome: FileOutcome, out: Path) -> None:
        for part in outcome.parts:
            for rel in filter(None, (part.mesh, part.step, part.objects)):
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / key / rel, out / rel)
