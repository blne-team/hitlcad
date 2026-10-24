"""CAD backend plugin interface.

A backend runs a design's code (`interfaces.py`, part files, `assembly.py`) through the model runtime
in its own child process, in two steps:

    plan          evaluate every interface value and run assembly.py: the placements, before geometry
    build_parts   run part files; export each part in its own frame (an STL that analyses measure, a GLB
                  of its named objects in their colours, and a STEP when the backend is B-rep), with its
                  connector frames and what the code read

Everything after that (assembly, measurement, analyses, exports) is backend-independent, except that a
backend may also write the built assembly in its own format (`write_assembly`) from the parts' files.
Third-party backends register through the ``hitlcad.backends`` entry-point group.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from hitlcad.core.report import CatalogInfo, Reads
from hitlcad.core.types import Matrix, Model, Value


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
    error: str | None = None
    log: str = ""
    interfaces: dict[str, InterfaceOutcome] = Field(default_factory=dict)
    placements: dict[str, list[Matrix]] = Field(default_factory=dict)
    assembly_reads: Reads = Field(default_factory=Reads)


class BuiltPart(Model):
    name: str
    mesh: str = Field(description="STL in the part's own frame, mm")
    step: str | None = Field(default=None, description="STEP in the part's own frame (B-rep backends)")
    objects: str = Field(description="GLB of the part's named objects in their colours, in its own frame")
    connectors: dict[str, list[Matrix]] = Field(default_factory=dict)
    catalog: CatalogInfo | None = None
    valid: bool | None = Field(default=None, description="The kernel's own validity check, where it has one")


class FileOutcome(Model):
    file: str
    error: str | None = None
    cached: bool = Field(default=False, description="Restored from the part cache, not built")
    reads: Reads = Field(default_factory=Reads)
    parts: list[BuiltPart] = Field(default_factory=list)


class PartsOutcome(Model):
    error: str | None = Field(default=None, description="The process failed as a whole")
    log: str = ""
    files: list[FileOutcome] = Field(default_factory=list)


class CADBackend(ABC):
    name: str
    title: str
    #: One paragraph for the agent: what this backend is good and bad at.
    summary: str
    #: Its parts carry a B-rep (STEP): exact, with faces, edges and vertices to pick, number and measure between.
    brep: ClassVar[bool]

    @abstractmethod
    def availability(self) -> tuple[bool, str]:
        """(available, detail), so the service can report broken backends."""

    @abstractmethod
    def authoring_guide(self) -> str:
        """Markdown contract the agent follows when writing code for this backend."""

    @abstractmethod
    async def plan(self, src: Path, values: dict[str, Value], work: Path) -> PlanOutcome: ...

    @abstractmethod
    async def build_parts(
        self, src: Path, values: dict[str, Value], files: dict[str, dict[str, list[str]]], work: Path
    ) -> PartsOutcome:
        """Build `files` ({part file: {part: [declared connectors]}}) into `work`."""

    async def write_assembly(self, tree: dict[str, Any], out_dir: Path, work: Path) -> tuple[str, str] | None:
        """Write the built assembly in this backend's own format into `out_dir`, for designs with parts it
        built: (relative path, media type), or None when it has no format of its own. `tree` is
        `build.assemble.tree`: each design's parts by their files, its components, its placed instances."""
        return None

    def fingerprint(self) -> str:
        """What cached parts built by this backend depend on besides their inputs: its code and settings."""
        return self.name

    def describe(self) -> BackendInfo:
        ok, detail = self.availability()
        return BackendInfo(name=self.name, title=self.title, summary=self.summary, brep=self.brep, available=ok,
                           detail=detail)  # fmt: skip
