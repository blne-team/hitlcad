"""Builds and what they found: the build spec, the report, revisions.

Every analysis reports `Result`s. A result names the addresses it is about, so the design graph
and the tools present the findings of any analysis, including ones added later, without code of
their own.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from hitlcad.core.design import DesignSpec
from hitlcad.core.structure import Measured, Tolerances
from hitlcad.core.types import Matrix, Model, Value, utcnow

Status = Literal["pass", "fail", "unverified"]
Severity = Literal["error", "warning", "info"]


class BuildStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Result(Model):
    """One finding of one analysis."""

    analysis: str = Field(description="The analysis that found it: structure, connector, mate, interference, …")
    subject: str = Field(description="What it is about, in the address grammar")
    status: Status
    severity: Severity = "error"
    detail: str
    about: list[str] = Field(default_factory=list, description="Addresses it concerns: instances, parts, connectors")
    values: dict[str, float | str | bool] = Field(default_factory=dict)


class MeshMetrics(Model):
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    size: tuple[float, float, float]
    volume: float | None
    surface_area: float
    triangles: int
    bodies: int
    watertight: bool
    winding_consistent: bool


class Reads(Model):
    """What a piece of model code read while it ran: the exact inputs it depends on."""

    parameters: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list, description="lib/ modules it imported")


class ConnectorInfo(Model):
    kind: str
    frames: list[Matrix] = Field(description="In the part's own frame")
    measured: list[Measured] = Field(default_factory=list, description="One per frame")


class CatalogInfo(Model):
    provider: str = Field(description="The catalog provider that made the geometry, or 'model' when the part file did")
    designation: str = ""
    material: str | None = None
    metadata: dict[str, Value] = Field(
        default_factory=dict, description="What the provider knows: sizes, fastener data"
    )


class MassProperties(Model):
    mass_g: float
    centre_mm: tuple[float, float, float]
    inertia_g_mm2: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


class PartResult(Model):
    name: str
    file: str
    backend: str
    made: bool
    material: str | None
    process: str | None
    catalog: CatalogInfo | None = None
    cached: bool = Field(description="Reused from an earlier build whose inputs it read were the same")
    reads: Reads
    metrics: MeshMetrics
    mass: MassProperties | None = None
    connectors: dict[str, ConnectorInfo] = Field(default_factory=dict)


class InstanceInfo(Model):
    path: str = Field(description="Instance address in this design, e.g. 'planet[2]' or 'tilt'")
    part: str | None = Field(default=None, description="The part it places")
    component: str | None = Field(default=None, description="The component it places")
    transform: Matrix


class MotionRange(Model):
    """A mate's range of motion, for the panel to move its moving side through."""

    mate: str
    pair: str = Field(description="Which frames of a pattern, e.g. 'base.hinge ↔ lid.pin'")
    kind: Literal["angle", "travel"]
    start: float
    end: float
    continuous: bool
    current: float = Field(description="Where it sits as assembled")
    origin: tuple[float, float, float]
    axis: tuple[float, float, float]
    sign: float = Field(description="+1: the moving side turns with the motion about the axis; -1: against it")
    moving: list[str] = Field(description="The instances that move")


class InterfaceValue(Model):
    name: str
    value: Any
    reads: Reads


class BomLine(Model):
    level: int = Field(description="0 for this design's own parts, deeper for components")
    path: str = Field(description="Where it is used: the part, or component/part")
    number: str
    name: str
    quantity: int
    kind: Literal["made", "catalog", "component"]
    material: str | None = None
    process: str | None = None
    designation: str = ""
    supplier: str = ""
    mpn: str = ""


class Artifact(Model):
    """A file produced for a revision; ``path`` is relative to the revision directory."""

    name: str
    path: str
    media_type: str
    role: str = Field(
        description="source, mesh, exchange, view, part_mesh, part_exchange, objects, preview, log, report, or an exporter's name"
    )
    sha256: str
    bytes: int


class BuildReport(Model):
    status: BuildStatus
    configuration: str | None = None
    values: dict[str, Value] = Field(default_factory=dict, description="The parameter values built")
    duration_s: float
    error: str | None = None
    log_tail: str = ""
    metrics: MeshMetrics | None = Field(default=None, description="The whole placed assembly")
    mass: MassProperties | None = None
    interfaces: list[InterfaceValue] = Field(default_factory=list)
    assembly_reads: Reads = Field(default_factory=Reads)
    parts: list[PartResult] = Field(default_factory=list)
    instances: list[InstanceInfo] = Field(default_factory=list)
    components: dict[str, BuildReport] = Field(default_factory=dict, description="Each component's own build")
    results: list[Result] = Field(default_factory=list)
    motions: list[MotionRange] = Field(default_factory=list)
    bom: list[BomLine] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)

    def artifact(self, role: str) -> Artifact | None:
        return next((a for a in self.artifacts if a.role == role), None)

    def part(self, name: str) -> PartResult | None:
        return next((p for p in self.parts if p.name == name), None)

    def failing(self) -> list[Result]:
        return [r for r in self.results if r.status == "fail"]


class Revision(Model):
    id: int
    created_at: datetime = Field(default_factory=utcnow)
    origin: Literal["file", "parameter_edit"]
    parent: int | None = None
    spec: BuildSpec
    summary: str = ""
    report: BuildReport


class BuildSpec(Model):
    """Everything a build depends on: the design, its sources, the configuration, the tolerances, and
    the specs of the components it uses."""

    design: DesignSpec
    sources: dict[str, str] = Field(
        description="Relative path -> text, for interfaces.py, assembly.py, parts/ and lib/"
    )
    assets: dict[str, str] = Field(default_factory=dict, description="Relative path -> sha256 of vendor/ files")
    configuration: str | None = None
    overrides: dict[str, Value] = Field(default_factory=dict, description="Parameter values a parent design binds")
    tolerances: Tolerances
    components: dict[str, BuildSpec] = Field(default_factory=dict)

    def values(self) -> dict[str, Value]:
        values = self.design.values(self.configuration)
        by_name = {p.name: p for p in self.design.parameters}
        return values | {k: by_name[k].coerce(v) for k, v in self.overrides.items()}

    @property
    def key(self) -> str:
        """Hash of everything but briefs: a build whose key matches the last one's would build the same."""
        return hashlib.sha256(
            json.dumps(_without_briefs(self.model_dump(mode="json")), sort_keys=True).encode()
        ).hexdigest()


def _without_briefs(data: dict[str, Any]) -> dict[str, Any]:
    data["design"].pop("brief", None)
    data["components"] = {k: _without_briefs(v) for k, v in data["components"].items()}
    return data


Revision.model_rebuild()
BuildReport.model_rebuild()
