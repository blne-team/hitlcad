"""CAD backend plugin interface.

A backend runs a design's code (`interfaces.py`, part files, `assembly.py`) in its build workers
(`runtime.serve`): processes of its own Python, kept between a design's builds (`backends.workers`). A plugin
gives the backend's worker, an entry script serving jobs with its `runtime.Adapter` (what its part values are,
how they export), and this class's description of it: how to start it, what its parts depend on, and what
hitlcad completes of its parts in its own process. The jobs are hitlcad's:

    plan    evaluate every interface value and run assembly.py: the placements, before geometry
    parts   run one part file; export each of its parts in its own frame (an STL that analyses measure, a GLB
            of its named objects in their colours, a STEP when the backend is B-rep, and its view: the part
            tessellated for the 3D view, `geometry.cadview.PartView`), with its connector frames and what the
            code read

Everything after that (assembly, measurement, analyses, exports) is backend-independent, except that a
backend may also write the built assembly in its own format (`write_assembly`) from the parts' files, in a job
of its own. A plugin gives a backend from its `hitlcad_backends` hook (hitlcad's own: `hitlcad.builtin`). What
went wrong comes back as a `Diagnostic`.

Between hitlcad and a worker, a job is a folder: job.json {"mode", "src", "params", "files", "paths" (where the
model packages import from), …} in, result.json {"ok", "error": {"code", "message", "where"} | None, …the mode's
fields} out.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import signal
from abc import ABC, abstractmethod
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from hitlcad.backends.workers import Ticket, Workers
from hitlcad.config import Settings
from hitlcad.core.report import CatalogInfo, Reads
from hitlcad.core.types import Matrix, Value
from hitlcad.execution import runtime
from hitlcad.execution.executor import Executor, LocalExecutor, last_lines
from hitlcad.kernel.errors import BUILD_CRASHED, BUILD_MEMORY, BUILD_TIME, Diagnostic, reported
from hitlcad.kernel.model import Model
from hitlcad.kernel.plugins import ModelPackage, registry


class BackendInfo(Model):
    name: str
    title: str
    summary: str
    brep: bool
    available: bool
    detail: str


class InterfaceOutcome(Model):
    value: Any
    reads: Reads


class PlanOutcome(Model):
    error: Diagnostic | None = None
    log: str = ""
    interfaces: dict[str, InterfaceOutcome] = Field(default_factory=dict)
    placements: dict[str, list[Matrix]] = Field(default_factory=dict)
    assembly_reads: Reads = Field(default_factory=Reads)


class BuiltPart(Model):
    name: str
    mesh: str = Field(description="STL in the part's own frame, mm")
    step: str | None = Field(default=None, description="STEP in the part's own frame (B-rep backends)")
    objects: str = Field(description="GLB of the part's named objects in their colours, in its own frame")
    view: str = Field(description="The part tessellated for the 3D view (`geometry.cadview.PartView`), in its own "
                      "frame: a B-rep part's faces, edges and vertices numbered as its STEP's")  # fmt: skip
    connectors: dict[str, list[Matrix]] = Field(default_factory=dict)
    catalog: CatalogInfo | None = None
    valid: bool | None = Field(default=None, description="The kernel's own validity check, where it has one")

    def files(self) -> list[str]:
        """Its files, relative to the directory it was built in."""
        return [f for f in (self.mesh, self.step, self.objects, self.view) if f]


class FileOutcome(Model):
    file: str
    error: Diagnostic | None = None
    cached: bool = Field(default=False, description="Restored from the part cache, not built")
    reads: Reads = Field(default_factory=Reads)
    parts: list[BuiltPart] = Field(default_factory=list)


class CADBackend(ABC):
    name: str
    title: str
    #: One paragraph for the agent: what this backend is good and bad at.
    summary: str
    #: Its parts carry a B-rep (STEP): exact, with faces, edges and vertices to pick, number and measure between.
    brep: ClassVar[bool]
    #: Its worker's entry script: `runtime.serve` with the backend's adapter.
    entry: ClassVar[Path]

    def __init__(self, settings: Settings, executor: Executor | None = None) -> None:
        self.settings = settings
        self.workers = Workers(executor or LocalExecutor(), settings.workers)

    @abstractmethod
    def availability(self) -> tuple[bool, str]:
        """(available, detail), so the service can report broken backends."""

    @abstractmethod
    def authoring_guide(self) -> str:
        """Markdown contract the agent follows when writing code for this backend."""

    @abstractmethod
    def command(self) -> list[str]:
        """argv that starts a worker: its entry script, reading its jobs' paths on stdin."""

    def packages(self) -> list[ModelPackage]:
        """The plugins' model packages its part files may import."""
        return [p for p in registry().model_packages if self.name in p.backends]

    def code(self) -> list[Path]:
        """The code its parts are built with besides the design's: its entry script, the model runtime, the SDK and
        the model packages."""
        sdk = Path(runtime.__file__).parents[1] / "sdk" / "__init__.py"
        return [self.entry, Path(runtime.__file__), sdk, *(f for p in self.packages() for f in p.files())]

    def libraries(self) -> dict[str, str | None]:
        """The versions of the libraries its workers build with."""
        return {}

    @cached_property
    def _fingerprint(self) -> str:
        data = {"name": self.name, "code": [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.code()],
                "libraries": self.libraries(),
                "mesh": [self.settings.mesh_tolerance_mm, self.settings.mesh_angular_tolerance]}  # fmt: skip
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def fingerprint(self) -> str:
        """What cached parts built by this backend depend on besides their inputs: its code, its libraries'
        versions and the mesh tolerances, read once."""
        return self._fingerprint

    def complete_parts(self, parts: list[dict[str, Any]], work: Path) -> None:
        """Write in `work` what of its built parts (`BuiltPart` fields) its workers cannot, and name it in `parts`:
        nothing, where they write it all."""
        return None

    async def write_assembly(self, ticket: Ticket, design: Path, tree: dict[str, Any], out_dir: Path, work: Path
                             ) -> tuple[str, str] | None:  # fmt: skip
        """Write `design`'s built assembly in this backend's own format into `out_dir`, for designs with parts it
        built: (relative path, media type), or None when it has no format of its own. `tree` is
        `build.assemble.tree`: each design's parts by their files, its components, its placed instances."""
        return None

    def warm(self) -> None:
        """Start a spare worker before a build asks (`Workers.warm`), once the backend is available."""
        if self.availability()[0]:
            self.workers.warm(self.command())

    async def release(self, designs: list[Path]) -> None:
        """A server is done with these design folders: their idle workers stop, and a spare takes a place freed."""
        await self.workers.release({str(d.resolve()) for d in designs})
        self.warm()

    async def close(self) -> None:
        await self.workers.close()

    def describe(self) -> BackendInfo:
        ok, detail = self.availability()
        return BackendInfo(name=self.name, title=self.title, summary=self.summary, brep=self.brep, available=ok,
                           detail=detail)  # fmt: skip

    async def plan(self, ticket: Ticket, design: Path, src: Path, values: dict[str, Value], work: Path) -> PlanOutcome:
        """The interface values and placements of `design` (its folder), from its sources `src`."""
        result, log = await self.job(
            ticket, design, work, {"mode": "plan", "src": str(src.resolve()), "params": values}
        )
        if not result["ok"]:
            return PlanOutcome(error=result["error"], log=log)
        return PlanOutcome.model_validate({**{k: v for k, v in result.items() if k != "ok"}, "log": log})

    async def build_file(self, ticket: Ticket, design: Path, src: Path, values: dict[str, Value], file: str,
                         declared: dict[str, list[str]], work: Path) -> tuple[FileOutcome, str]:  # fmt: skip
        """Build part file `file` of `design` ({part: [declared connectors]}) into `work`, and the worker's log."""
        job = {"mode": "parts", "src": str(src.resolve()), "params": values, "files": {file: declared},
               "mesh_tolerance_mm": self.settings.mesh_tolerance_mm,
               "mesh_angular_tolerance": self.settings.mesh_angular_tolerance}  # fmt: skip
        result, log = await self.job(ticket, design, work, job, file)
        if not result["ok"]:
            return FileOutcome(file=file, error=result["error"]), log
        (built,) = result["files"]
        await asyncio.to_thread(self.complete_parts, built.get("parts", []), work.resolve())
        return FileOutcome.model_validate(built), log

    async def job(self, ticket: Ticket, design: Path, work: Path, job: dict[str, Any], file: str | None = None
                  ) -> tuple[dict[str, Any], str]:  # fmt: skip
        """Run a job of `design` in `work` in one of its workers (for its part file `file`, if it is one): its
        result.json, errors as diagnostics, and what it printed."""
        work = work.resolve()  # the worker works in the job's folder: paths handed to it must not be relative to ours
        work.mkdir(parents=True, exist_ok=True)
        job_file = work / "job.json"
        paths = sorted({str(p.root) for p in self.packages()})
        job_file.write_text(json.dumps(job | {"paths": paths, "cache_mb": self.settings.worker_cache_mb}),
                            encoding="utf-8")  # fmt: skip
        result_file = work / "result.json"
        result_file.unlink(missing_ok=True)
        run = await self.workers.run(ticket, self.command(), str(design.resolve()), file, job_file,
                                     timeout_s=self.settings.build_timeout_s, memory_mb=self.settings.build_memory_mb)  # fmt: skip
        if run.stopped:
            kind = BUILD_TIME if run.limit == "time" else BUILD_MEMORY
            return {"ok": False, "error": kind.diagnostic(f"the build {run.stopped}")}, run.output
        if not result_file.is_file():
            ended = BUILD_CRASHED.diagnostic(f"{_ended(run.returncode)}:\n{last_lines(run.output)}")
            return {"ok": False, "error": ended}, run.output
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if not result["ok"]:
            result["error"] = reported(result["error"])
        for built in result.get("files", []):
            if built.get("error"):
                built["error"] = reported(built["error"])
        return result, run.output


# Signals that end a worker on POSIX, and why they usually do; by name, as Windows has none of them.
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
    """How the worker ended, when it left no result."""
    if code < 0:
        why = {getattr(signal, name, None): why for name, why in _SIGNALS.items()}.get(-code)
        return f"the build process {why or f'was ended by signal {-code} ({signal.strsignal(-code)})'}"
    if code in _NTSTATUS:
        return f"the build process {_NTSTATUS[code]}"
    return f"the build process exited with code {code}"
