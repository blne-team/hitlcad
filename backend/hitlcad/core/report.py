"""Builds and what they found: the build spec, the report, revisions.

Every analysis reports `Result`s. A result names the addresses it is about, so the design graph
and the tools present the findings of any analysis, including ones added later, without code of
their own. One mistake can fail several checks (an impeller too large overlaps its duct and misses
the clearance its requirement asks), so a result also names its `cause` where its analysis can tell,
and the report groups the results that share one into a `Problem`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from hitlcad.core.address import InstancePath
from hitlcad.core.design import DesignSpec
from hitlcad.core.structure import Measured, Tolerances
from hitlcad.core.types import Matrix, Value, utcnow
from hitlcad.kernel.errors import Location
from hitlcad.kernel.model import Model

Status = Literal["pass", "fail", "unverified"]
#: What made a revision: files saved, the panel's parameters, or a review (the human watched a loop's motion)
Origin = Literal["file", "parameter_edit", "review"]
Severity = Literal["error", "warning", "info"]


class BuildStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Cause:
    """The keys of `Result.cause`: problems more than one analysis can see, each by what it is about. Instances
    are named by their top-level instance (a part's or a component's), so a component's leaves meet the
    requirements that name the component.

        overlap:A|B      A and B share volume where they sit
        contact:M:A|B    moving mate M brings A against B
        mate:M:A|B       mate M's frames on A and B disagree: one of them is not where M puts it
        loop:L1,L2,…     those mates (links, by their frames) close a loop of motion, swept as one
    """

    @staticmethod
    def overlap(a: str, b: str) -> str:
        return f"overlap:{_pair(a, b)}"

    @staticmethod
    def contact(mate: str, a: str, b: str) -> str:
        return f"contact:{mate}:{_pair(a, b)}"

    @staticmethod
    def mate(mate: str, a: str, b: str) -> str:
        return f"mate:{mate}:{_pair(a, b)}"

    @staticmethod
    def loop(mates: list[str]) -> str:
        return "loop:" + ",".join(sorted(set(mates)))


def _pair(a: str, b: str) -> str:
    return "|".join(sorted(str(InstancePath.parse(x).head) for x in (a, b)))


class Result(Model):
    """One finding of one analysis."""

    analysis: str = Field(description="The analysis that found it: build, structure, connector, mate, …")
    code: str = Field(description="The kind of check, from the error catalog: 'mate.fit', 'model.exception'")
    subject: str = Field(description="What it is about, in the address grammar")
    status: Status
    severity: Severity = Field(default="error", description="info: not a check (an overlap expected, a mate held)")
    detail: str
    where: Location | None = Field(default=None, description="Where in the design's files, when it is the code's")
    about: list[str] = Field(default_factory=list, description="Addresses it concerns: instances, parts, connectors")
    values: dict[str, float | str | bool] = Field(default_factory=dict)
    cause: str = Field(default="", description="The problem it follows from, where other analyses may see it too "
                       "(`Cause`: 'overlap:duct|impeller'); empty, it is its own")  # fmt: skip


def results_key(results: list[Result]) -> str:
    """What a list of results says, as a key: two builds that fail alike have the same."""
    return hashlib.sha256(json.dumps([r.model_dump(mode="json") for r in results], sort_keys=True).encode()).hexdigest()


class Finding(Model):
    """Results alike but for their pattern indices and numbers (screw[0] … screw[3] each too long), as one."""

    results: list[int] = Field(description="Indices into the report's results; the first stands for the rest")
    indices: list[int] = Field(description="The pattern indices they are at")


class Problem(Model):
    """The results that share a cause, and problems alike but for their pattern indices, as one."""

    status: Literal["fail", "unverified"]
    severity: Severity = Field(description="error, or warning when every failing check in it is a warning")
    findings: list[Finding] = Field(description="The first analysis to run that saw it leads; the rest saw it too")


class Tally(Model):
    """A report's results counted by problem; info results are not checks."""

    problems: int = Field(default=0, description="Failing problems")
    failing: int = Field(default=0, description="Failing checks in them")
    warnings: int = Field(default=0, description="Failing problems of warnings only")
    unverified: int = Field(default=0, description="Problems no check could settle")
    passing: int = Field(default=0, description="Passing checks")
    text: str = Field(default="", description="As the agent reads it: '2 problems (5 failing checks), 1 unverified; "
                      "147 checks pass'")  # fmt: skip


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


_NUMBER = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?")
_INDEX = re.compile(r"\[(\d+)\]")


def _shape(r: Result) -> tuple[str, ...]:
    """What a result says but for its numbers: results of the same shape differ by pattern index and measure."""
    return r.analysis, r.code, r.status, r.severity, _NUMBER.sub("#", r.subject), _NUMBER.sub("#", r.detail)


def problems(results: list[Result]) -> list[Problem]:
    """The results that do not pass, one problem per cause, failing errors first, then warnings, then the
    unverified. Analyses run cause before effect (structure, parts, connectors, mates, … requirements: see
    `analysis.base.default_analyses`), so within a problem the earliest analysis's results lead."""
    rank: dict[str, int] = {}
    by_cause: dict[str, list[int]] = {}
    for i, r in enumerate(results):
        rank.setdefault(r.analysis, len(rank))
        if r.status != "pass":
            by_cause.setdefault(r.cause or f"#{i}", []).append(i)
    alike: dict[tuple[Any, ...], list[list[list[int]]]] = {}
    for members in by_cause.values():
        members.sort(key=lambda i: (rank[results[i].analysis], results[i].status != "fail", i))
        findings: dict[tuple[str, ...], list[int]] = {}
        for i in members:
            findings.setdefault(_shape(results[i]), []).append(i)
        alike.setdefault(tuple(findings), []).append(list(findings.values()))
    out = []
    for copies in alike.values():
        merged = [sum(column, []) for column in zip(*copies, strict=True)]
        members = [results[i] for i in sum(merged, [])]
        failing = [r for r in members if r.status == "fail"]
        out.append(Problem(
            status="fail" if failing else "unverified",
            severity="error" if any(r.severity == "error" for r in failing or members) else "warning",
            findings=[Finding(results=ids, indices=sorted({k for i in ids if (k := _index(results[i])) is not None}))
                      for ids in merged],
        ))  # fmt: skip
    order = {("fail", "error"): 0, ("fail", "warning"): 1}
    return sorted(out, key=lambda p: (order.get((p.status, p.severity), 2), p.findings[0].results[0]))


def _index(r: Result) -> int | None:
    found = _INDEX.search(r.subject) or _INDEX.search(r.detail)
    return int(found.group(1)) if found else None


def tally(results: list[Result], found: list[Problem]) -> Tally:
    errors = [p for p in found if p.status == "fail" and p.severity == "error"]
    t = Tally(
        problems=len(errors),
        failing=sum(results[i].status == "fail" for p in errors for f in p.findings for i in f.results),
        warnings=sum(p.status == "fail" and p.severity == "warning" for p in found),
        unverified=sum(p.status == "unverified" for p in found),
        passing=sum(r.status == "pass" and r.severity != "info" for r in results),
    )
    said = [f"{_count(t.problems, 'problem')} ({_count(t.failing, 'failing check')})" if t.problems else "no problems"]
    said += [_count(t.warnings, "warning")] if t.warnings else []
    said += [f"{t.unverified} unverified"] if t.unverified else []
    t.text = f"{', '.join(said)}; {_count(t.passing, 'check')} pass{'es' if t.passing == 1 else ''}"
    return t


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


class MotionDrive(Model):
    """Instances that move together through a range, about or along their own axis, by a law of the range's
    motion."""

    kind: Literal["angle", "travel"]
    origin: tuple[float, float, float]
    axis: tuple[float, float, float]
    law: list[tuple[float, float]] = Field(
        description="(the range's motion from where it sits, theirs) at points ascending over the slider's span, "
        "straight between them; degrees or mm, negative: against the axis"
    )
    instances: list[str]


class MotionPose(Model):
    """Instances that move together through a loop's positions: their motion from as assembled at each, 3 × 4 by
    rows."""

    instances: list[str]
    frames: list[list[float]]


class MotionRange(Model):
    """A mate's range of motion, for the panel to move what it moves through: about fixed axes (`drives`), or a
    loop's solved positions (`values`, `poses`)."""

    mate: str
    pair: str = Field(description="Which frames of a pattern, e.g. 'base.hinge ↔ lid.pin'")
    kind: Literal["angle", "travel"]
    start: float
    end: float
    continuous: bool
    current: float = Field(description="Where it sits as assembled")
    drives: list[MotionDrive] = Field(
        default_factory=list,
        description="What moves: the mate's moving side, then what relations drive from it (a gear on its own axis)",
    )
    values: list[float] = Field(
        default_factory=list, description="A loop's: the driving mate at each solved position shown, ascending"
    )
    poses: list[MotionPose] = Field(default_factory=list, description="A loop's: what moves, at those positions")
    key: str = Field(default="", description="A loop's sweep: what a human's watch of it is of")


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
        description="source, mesh, exchange, native, view, part_mesh, part_exchange, objects, preview, log, report, or an "
        "exporter's name"
    )
    label: str = Field(default="", description="What the file is, for the panel's file list; empty for the files it "
                       "shows its own way (sources, the 3D view, the preview)")  # fmt: skip
    sha256: str
    bytes: int


class BuildReport(Model):
    """A build: what it built and what it found. A build that fails, fails because it cannot produce geometry, and
    its results say why."""

    status: BuildStatus
    configuration: str | None = None
    values: dict[str, Value] = Field(default_factory=dict, description="The parameter values built")
    duration_s: float
    metrics: MeshMetrics | None = Field(default=None, description="The whole placed assembly")
    mass: MassProperties | None = None
    interfaces: list[InterfaceValue] = Field(default_factory=list)
    assembly_reads: Reads = Field(default_factory=Reads)
    parts: list[PartResult] = Field(default_factory=list)
    instances: list[InstanceInfo] = Field(default_factory=list)
    components: dict[str, BuildReport] = Field(default_factory=dict, description="Each component's own build")
    results: list[Result] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list, description="The results that do not pass, by cause")
    tally: Tally = Field(default_factory=Tally)
    motions: list[MotionRange] = Field(default_factory=list)
    bom: list[BomLine] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)

    @model_validator(mode="after")
    def _grouped(self) -> BuildReport:
        """Problems and tally follow from the results, so a report read back is grouped by today's rules."""
        self.problems = problems(self.results)
        self.tally = tally(self.results, self.problems)
        return self

    def artifact(self, role: str) -> Artifact | None:
        return next((a for a in self.artifacts if a.role == role), None)

    def part(self, name: str) -> PartResult | None:
        return next((p for p in self.parts if p.name == name), None)

    def failing(self) -> list[Result]:
        return [r for r in self.results if r.status == "fail"]

    def lead(self) -> Result | None:
        """The result that leads the first problem: what a failed build failed on."""
        return self.results[self.problems[0].findings[0].results[0]] if self.problems else None


class RevisionInfo(Model):
    """A revision as a design's state lists it: what it is and how it went. `Revision` is all of it."""

    id: int
    created_at: datetime
    origin: Origin
    parent: int | None = Field(description="The configuration's latest successful revision before it")
    configuration: str | None
    summary: str
    status: BuildStatus
    tally: Tally
    failure: Result | None = Field(description="What a failed build failed on: the result that leads its problems")
    key: str | None = Field(
        description="Its build spec's key (`BuildSpec.key`); none when design.json did not validate"
    )


class Revision(Model):
    id: int
    created_at: datetime = Field(default_factory=utcnow)
    origin: Origin
    parent: int | None = None
    configuration: str | None = None
    spec: BuildSpec | None = Field(description="What it built from; none when design.json did not validate")
    summary: str = ""
    report: BuildReport

    def info(self) -> RevisionInfo:
        r = self.report
        return RevisionInfo(id=self.id, created_at=self.created_at, origin=self.origin, parent=self.parent,
                            configuration=self.configuration, summary=self.summary, status=r.status, tally=r.tally,
                            failure=r.lead() if r.status == BuildStatus.FAILED else None,
                            key=self.spec.key if self.spec else None)  # fmt: skip


@dataclass(frozen=True)
class Earlier:
    """What an earlier revision of the same configuration failed: (code, subject) of each result that did not
    pass, so a build can say which problems it still has."""

    revision: int
    failing: frozenset[tuple[str, str]]


class BuildSpec(Model):
    """Everything a build depends on: the design, its sources, the configuration, the tolerances, and
    the specs of the components it uses."""

    design: DesignSpec
    sources: dict[str, str] = Field(
        description="Relative path -> text, for interfaces.py, assembly.py, parts/ and lib/"
    )
    assets: dict[str, str] = Field(default_factory=dict, description="Relative path -> sha256 of vendor/ files")
    checked: dict[str, str] = Field(default_factory=dict, description="Relative path -> sha256 of the files its "
                                    "requirement checks read (`Check.files`: reference pictures); '' for a missing one")  # fmt: skip
    configuration: str | None = None
    overrides: dict[str, Value] = Field(default_factory=dict, description="Parameter values a parent design binds")
    tolerances: Tolerances
    components: dict[str, BuildSpec] = Field(default_factory=dict)
    plugins: dict[str, str] = Field(default_factory=dict, description="The installed plugins it was built with, but "
                                    "hitlcad's own: distribution -> version")  # fmt: skip

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
