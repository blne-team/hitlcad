"""Base class for backends that run a design's code in a child process via an entry script.

Protocol between parent and entry script: the entry, started before its job is known, reads the job's path on
stdin; the job's folder is its work directory.

    in:  job.json {"mode": "plan" | "parts", "src": <design sources>, "params": {...}, "files": {...},
                   "paths": [where the model packages import from], ...}
    out: result.json {"ok": bool, "error": {"code", "message", "where"} | None, ...the mode's fields}
"""

from __future__ import annotations

import hashlib
import json
import signal
from abc import abstractmethod
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

from hitlcad.backends.base import CADBackend, PartsOutcome, PlanOutcome
from hitlcad.core.types import Value
from hitlcad.execution import runtime
from hitlcad.execution.executor import Executor, LocalExecutor, last_lines
from hitlcad.kernel.errors import BUILD_CRASHED, BUILD_MEMORY, BUILD_TIME, reported
from hitlcad.kernel.plugins import ModelPackage, registry


class SubprocessBackend(CADBackend):
    entry: ClassVar[Path]  # the entry script

    def __init__(
        self, *, executor: Executor | None = None, timeout_s: float, memory_mb: int,
        mesh_tolerance_mm: float, mesh_angular_tolerance: float, warm: int = 0,
    ) -> None:  # fmt: skip
        self.executor = executor or LocalExecutor(warm=warm)
        self.timeout_s = timeout_s
        self.memory_mb = memory_mb
        self.mesh_tolerance_mm = mesh_tolerance_mm
        self.mesh_angular_tolerance = mesh_angular_tolerance

    def packages(self) -> list[ModelPackage]:
        """The plugins' model packages its part files may import."""
        return [p for p in registry().model_packages if self.name in p.backends]

    def code(self) -> list[Path]:
        """The code its child processes run besides the design's: its entry script, the model runtime, the SDK and
        the model packages."""
        sdk = Path(runtime.__file__).parents[1] / "sdk" / "__init__.py"
        return [self.entry, Path(runtime.__file__), sdk, *(f for p in self.packages() for f in p.files())]

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
    def command(self) -> list[str]:
        """argv that starts a build process: its entry script, which waits for its job's path on stdin."""

    def warm(self) -> None:
        """Start the build processes kept ready, once it is available."""
        if self.availability()[0]:
            self.executor.warm(self.command())

    async def close(self) -> None:
        await self.executor.close()

    async def _run(self, work: Path, job: dict[str, Any]) -> tuple[dict[str, Any], str]:
        # The child runs with cwd=work, so paths handed to it must not be relative to ours.
        work = work.resolve()
        work.mkdir(parents=True, exist_ok=True)
        job_file = work / "job.json"
        paths = sorted({str(p.root) for p in self.packages()})  # where the model packages import from
        job_file.write_text(json.dumps(job | {"paths": paths}), encoding="utf-8")
        result_file = work / "result.json"
        result_file.unlink(missing_ok=True)
        run = await self.executor.run_job(self.command(), job_file, timeout_s=self.timeout_s, memory_mb=self.memory_mb)
        if run.stopped:
            kind = BUILD_TIME if run.limit == "time" else BUILD_MEMORY
            return {"ok": False, "error": kind.diagnostic(f"the build {run.stopped}")}, run.output
        if not result_file.is_file():
            ended = BUILD_CRASHED.diagnostic(f"{_ended(run.returncode)}:\n{last_lines(run.output)}")
            return {"ok": False, "error": ended}, run.output
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if not result["ok"]:
            result["error"] = reported(result["error"])
        for file in result.get("files", []):
            if file.get("error"):
                file["error"] = reported(file["error"])
        return result, run.output

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
