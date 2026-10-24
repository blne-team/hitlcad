"""CAD backend plugin interface.

A backend runs a design's code (`interfaces.py`, part files, `assembly.py`) through the model runtime
in its own child process, in two steps:

    plan          evaluate every interface value and run assembly.py: the placements, before geometry
    build_parts   run part files; export each part in its own frame (mesh.stl always, a STEP when the
                  backend is B-rep), with its connector frames and what the code read

Everything after that (assembly, measurement, analyses, exports) is backend-independent. Third-party
backends register through the ``hitlcad.backends`` entry-point group.
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
    objects: str | None = Field(default=None, description="GLB of the part's named objects (mesh backends)")
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
    #: Its parts carry a B-rep (STEP): connector features and holes can be measured on them.
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

    def fingerprint(self) -> str:
        """What cached parts built by this backend depend on besides their inputs: its code and settings."""
        return self.name

    def describe(self) -> BackendInfo:
        ok, detail = self.availability()
        return BackendInfo(name=self.name, title=self.title, summary=self.summary, brep=self.brep, available=ok,
                           detail=detail)  # fmt: skip
