"""Base class for backends that run a design's code in a child process via an entry script.

Protocol between parent and entry script (cwd = the work directory):

    in:  job.json {"mode": "plan" | "parts", "src": <design sources>, "params": {...}, "files": {...}, ...}
    out: result.json {"ok": bool, "error": str | None, ...the mode's fields}
"""

from __future__ import annotations

import hashlib
import json
import signal
from abc import abstractmethod
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

from hitlcad.backends import runtime
from hitlcad.backends.base import CADBackend, PartsOutcome, PlanOutcome
from hitlcad.backends.executor import Executor, LocalExecutor, last_lines
from hitlcad.core.types import Value

#: hitlcad's own code that model code runs with, besides an entry script and the model runtime: the SDK and the
#: catalog providers, relative to the hitlcad package.
MODEL_CODE = ("sdk/__init__.py", "catalogs/*.py")


class SubprocessBackend(CADBackend):
    entry: ClassVar[Path]  # the entry script

    def __init__(
        self, *, executor: Executor | None = None, timeout_s: float, memory_mb: int,
        mesh_tolerance_mm: float, mesh_angular_tolerance: float,
    ) -> None:  # fmt: skip
        self.executor = executor or LocalExecutor()
        self.timeout_s = timeout_s
        self.memory_mb = memory_mb
        self.mesh_tolerance_mm = mesh_tolerance_mm
        self.mesh_angular_tolerance = mesh_angular_tolerance

    def code(self) -> list[Path]:
        """The files of hitlcad's that its child processes run: its entry script, the model runtime, the SDK and the
        catalog providers."""
        root = Path(runtime.__file__).parents[1]
        return [self.entry, Path(runtime.__file__), *sorted(p for pattern in MODEL_CODE for p in root.glob(pattern))]

    def libraries(self) -> dict[str, str | None]:
        """The versions of the libraries its child processes build with."""
        return {}

    @cached_property
    def _fingerprint(self) -> str:
        data = {"name": self.name, "code": [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.code()],
                "libraries": self.libraries(), "mesh": [self.mesh_tolerance_mm, self.mesh_angular_tolerance]}  # fmt: skip
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def fingerprint(self) -> str:
        """Its code, its libraries' versions and the mesh tolerances, read once."""
        return self._fingerprint

    @abstractmethod
    def command(self, job_file: Path) -> list[str]:
        """argv that runs the entry script for ``job_file`` (cwd is the work directory)."""

    async def _run(self, work: Path, job: dict[str, Any]) -> tuple[dict[str, Any], str]:
        # The child runs with cwd=work, so paths handed to it must not be relative to ours.
        work = work.resolve()
        work.mkdir(parents=True, exist_ok=True)
        job_file = work / "job.json"
        job_file.write_text(json.dumps(job), encoding="utf-8")
        result_file = work / "result.json"
        result_file.unlink(missing_ok=True)
        run = await self.executor.run(self.command(job_file), work, timeout_s=self.timeout_s, memory_mb=self.memory_mb)
        if run.stopped:
            return {"ok": False, "error": f"the build {run.stopped}"}, run.output
        if not result_file.is_file():
            return {"ok": False, "error": f"{_ended(run.returncode)}:\n{last_lines(run.output)}"}, run.output
        return json.loads(result_file.read_text(encoding="utf-8")), run.output

    async def plan(self, src: Path, values: dict[str, Value], work: Path) -> PlanOutcome:
        result, log = await self._run(work, {"mode": "plan", "src": str(src.resolve()), "params": values})
        if not result["ok"]:
            return PlanOutcome(error=result["error"], log=log)
        return PlanOutcome.model_validate({**{k: v for k, v in result.items() if k != "ok"}, "log": log})

    async def build_parts(
        self, src: Path, values: dict[str, Value], files: dict[str, dict[str, list[str]]], work: Path
    ) -> PartsOutcome:
        job = {
            "mode": "parts",
            "src": str(src.resolve()),
            "params": values,
            "files": files,
            "mesh_tolerance_mm": self.mesh_tolerance_mm,
            "mesh_angular_tolerance": self.mesh_angular_tolerance,
        }
        result, log = await self._run(work, job)
        if not result["ok"]:
            return PartsOutcome(error=result["error"], log=log)
        return PartsOutcome(files=result["files"], log=log)


# Signals that end a build process on POSIX, and why they usually do; by name, as Windows has none of them.
_SIGNALS = {
    "SIGKILL": "was killed by the system, most often for running out of memory",
    "SIGSEGV": "crashed (segmentation fault)",
    "SIGABRT": "aborted, often for running out of memory",
}
# Windows ends a crashed process with an NTSTATUS as its exit code.
_NTSTATUS = {
    0xC0000005: "crashed (access violation)",
    0xC0000409: "aborted (a fail-fast exception)",
}


def _ended(code: int) -> str:
    """How the build process ended, when it left no result."""
    if code < 0:
        why = {getattr(signal, name, None): why for name, why in _SIGNALS.items()}.get(-code)
        return f"the build process {why or f'was ended by signal {-code} ({signal.strsignal(-code)})'}"
    if code in _NTSTATUS:
        return f"the build process {_NTSTATUS[code]}"
    return f"the build process exited with code {code}"
