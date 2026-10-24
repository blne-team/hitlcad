"""CAD backend plugin interface.

A backend turns model source + parameter values into files inside an output directory:

    mesh.stl     triangulated result in millimetres, Z up (required, used for inspection)
    <native>     optional native/exchange formats (STEP, .blend, ...)

Everything after that (inspection, the 3D view, rendering, artifact hashing) is backend-independent.
Third-party backends register through the ``hitlcad.backends`` entry-point group.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hitlcad.core.types import Model

MESH_FILE = "mesh.stl"


class BackendInfo(Model):
    name: str
    title: str
    summary: str
    available: bool
    detail: str


@dataclass(frozen=True)
class OutputFile:
    name: str
    media_type: str
    role: str  # one of Artifact.role


@dataclass
class BackendBuild:
    """What a backend produced. ``error`` set means the build failed."""

    files: list[OutputFile] = field(default_factory=list)
    log: str = ""
    error: str | None = None
    info: dict[str, Any] = field(default_factory=dict)


class CADBackend(ABC):
    name: str
    title: str
    #: One paragraph for the agent: what this backend is good and bad at.
    summary: str

    @abstractmethod
    def availability(self) -> tuple[bool, str]:
        """Return (available, detail) so the service can hide broken backends."""

    @abstractmethod
    def authoring_guide(self) -> str:
        """Markdown contract the agent must follow when writing source for this backend."""

    @abstractmethod
    async def build(self, source: str, params: dict[str, Any], out_dir: Path) -> BackendBuild:
        """Execute ``source`` with ``params``; write outputs into ``out_dir``."""

    def describe(self) -> BackendInfo:
        ok, detail = self.availability()
        return BackendInfo(name=self.name, title=self.title, summary=self.summary, available=ok, detail=detail)
