"""Domain model shared by every layer: parameters, briefs, builds, revisions, selection, pins.

All lengths are millimetres. Z is up.
"""

from __future__ import annotations

import hashlib
from abc import abstractmethod
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from hitlcad.analysis.assembly import Build, Part


def members(union: Any) -> tuple[type, ...]:
    """The types of an ``Annotated`` union (a single type is a union of one)."""
    inner = get_args(union)[0]
    return get_args(inner) or (inner,)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    # Serialized, every field is present: response schemas (and the panel's generated types) say so.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# --------------------------------------------------------------------------- parameters


class ParamSpec(Model):
    """A named, user-tunable input of a parametric model."""

    name: str = Field(description="Key in the params dict, e.g. wall_thickness")
    label: str = Field(default="", description="Human readable label")
    type: Literal["float", "int", "bool", "choice"] = "float"
    value: float | int | bool | str
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = Field(default="mm", description="Display unit, e.g. mm, deg, count")
    choices: list[str] | None = None
    description: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> ParamSpec:
        if self.type == "choice":
            if not self.choices or str(self.value) not in self.choices:
                raise ValueError(f"choice parameter {self.name!r} needs choices containing its value")
        elif self.type == "bool":
            if not isinstance(self.value, bool):
                raise ValueError(f"bool parameter {self.name!r} needs a boolean value")
        else:
            if isinstance(self.value, bool) or not isinstance(self.value, int | float):
                raise ValueError(f"numeric parameter {self.name!r} needs a numeric value")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError(f"parameter {self.name!r} has min > max")
        return self

    def coerce(self, raw: Any) -> float | int | bool | str:
        """Validate and coerce a user-supplied value against this spec."""
        if self.type == "bool":
            if isinstance(raw, bool):
                return raw
            raise ValueError(f"{self.name}: expected boolean")
        if self.type == "choice":
            if self.choices and str(raw) in self.choices:
                return str(raw)
            raise ValueError(f"{self.name}: expected one of {self.choices}")
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise ValueError(f"{self.name}: expected a number")
        value: float | int = int(raw) if self.type == "int" else float(raw)
        if self.min is not None and value < self.min:
            raise ValueError(f"{self.name}: {value} is below minimum {self.min}")
        if self.max is not None and value > self.max:
            raise ValueError(f"{self.name}: {value} is above maximum {self.max}")
        return value


def param_values(specs: list[ParamSpec]) -> dict[str, Any]:
    return {p.name: p.value for p in specs}


def apply_values(specs: list[ParamSpec], values: dict[str, Any]) -> list[ParamSpec]:
    """Return copies of ``specs`` with validated ``values`` applied. Unknown keys are rejected."""
    by_name = {p.name: p for p in specs}
    unknown = set(values) - set(by_name)
    if unknown:
        raise ValueError(f"unknown parameters: {sorted(unknown)}")
    return [p.model_copy(update={"value": p.coerce(values[p.name])}) if p.name in values else p for p in specs]


# --------------------------------------------------------------------------- brief


class Dimension(Model):
    name: str
    value: float
    unit: str = "mm"
    note: str = ""


class DesignBrief(Model):
    """What the design is meant to be. Lives in design.json; agents write it, the panel shows it."""

    title: str
    summary: str = Field(description="One paragraph describing the object and its purpose")
    backend: str = Field(description="CAD backend chosen for this design, e.g. build123d or blender")
    backend_rationale: str = ""
    key_dimensions: list[Dimension] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- requirements

Verdict = tuple[bool | None, str]  # (passed, detail); None: this build cannot tell


def _bounds(value: float, low: float | None, high: float | None, unit: str) -> Verdict:
    need = " and ".join(
        part for part in (f"≥ {low:g}" if low is not None else "", f"≤ {high:g}" if high is not None else "") if part
    )
    ok = (low is None or value >= low) and (high is None or value <= high)
    return ok, f"{value:.2f} {unit} (needs {need})"


class _Check(Model):
    """A requirement check: measured on every build. Its docstring and `example` are the agent's guide."""

    example: ClassVar[dict[str, Any]]

    @abstractmethod
    def verify(self, build: Build) -> Verdict: ...


class _Bounded(Model):
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounded(self) -> _Bounded:
        if self.min is None and self.max is None:
            raise ValueError("give min, max or both")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min is greater than max")
        return self


class _OnPart(_Check):
    part: str | None = Field(default=None, description="The assembly part it applies to; default: the whole design")


AXES = {"x": 0, "y": 1, "z": 2}
OVERLAP_MM3 = 1e-3  # parts sharing less than this touch; they do not overlap


class SizeCheck(_Bounded, _OnPart):
    """The extent along one axis, mm."""

    kind: Literal["size"]
    axis: Literal["x", "y", "z"]
    example = {"kind": "size", "axis": "z", "min": 9.9, "max": 10.1}

    def verify(self, build: Build) -> Verdict:
        passed, detail = _bounds(build.subject(self.part).metrics.size[AXES[self.axis]], self.min, self.max, "mm")
        return passed, f"{self.axis.upper()} {detail}"


class FitsWithinCheck(_OnPart):
    """Fits inside a box of this size (X, Y, Z mm), as it is modelled."""

    kind: Literal["fits_within"]
    box: tuple[float, float, float]
    example = {"kind": "fits_within", "box": [120, 80, 40]}

    def verify(self, build: Build) -> Verdict:
        size = build.subject(self.part).metrics.size
        over = [f"{axis.upper()} {size[i]:.2f} > {self.box[i]:g}" for axis, i in AXES.items() if size[i] > self.box[i]]
        box = " × ".join(f"{b:g}" for b in self.box)
        return not over, f"{' × '.join(f'{v:.2f}' for v in size)} mm in a {box} mm box" + (
            f": {', '.join(over)}" if over else ""
        )


class VolumeCheck(_Bounded, _OnPart):
    """Material volume, mm³."""

    kind: Literal["volume"]
    example = {"kind": "volume", "max": 50000}

    def verify(self, build: Build) -> Verdict:
        volume = build.subject(self.part).metrics.volume
        if volume is None:
            return None, "volume unknown: the mesh is not closed"
        return _bounds(volume, self.min, self.max, "mm³")


class MassCheck(_Bounded, _OnPart):
    """Mass in grams, from the volume and the material's density (PLA ~1.24, PETG ~1.27, aluminium 2.70 g/cm³)."""

    kind: Literal["mass"]
    density_g_cm3: float = Field(gt=0)
    example = {"kind": "mass", "density_g_cm3": 1.24, "max": 60}

    def verify(self, build: Build) -> Verdict:
        volume = build.subject(self.part).metrics.volume
        if volume is None:
            return None, "mass unknown: the mesh is not closed"
        passed, detail = _bounds(volume * self.density_g_cm3 / 1000, self.min, self.max, "g")
        return passed, f"{detail} at {self.density_g_cm3:g} g/cm³"


class HoleCheck(_OnPart):
    """At least `count` complete round holes of this diameter (through or blind), found on the B-rep (build123d)."""

    kind: Literal["hole"]
    diameter: float = Field(gt=0, description="mm")
    tol: float = Field(default=0.1, ge=0, description="mm either way")
    count: int = Field(default=1, ge=1)
    example = {"kind": "hole", "diameter": 6, "tol": 0.1, "count": 2}

    def verify(self, build: Build) -> Verdict:
        part = build.subject(self.part)
        if part.step is None:
            return None, "holes are found on B-rep (STEP) builds; this build is a mesh"
        holes = part.holes()
        matching = [h for h in holes if abs(h.diameter - self.diameter) <= self.tol]
        found = Counter(f"Ø{h.diameter:.2f}" for h in holes)
        inventory = ", ".join(f"{d} ×{n}" for d, n in sorted(found.items())) or "none"
        return len(matching) >= self.count, (
            f"{len(matching)} of Ø{self.diameter:g} ±{self.tol:g} mm (needs {self.count}); holes: {inventory}"
        )


class WallCheck(_OnPart):
    """No wall thinner than `min` mm."""

    kind: Literal["wall"]
    min: float = Field(gt=0)
    example = {"kind": "wall", "min": 2}

    def verify(self, build: Build) -> Verdict:
        thinnest = build.subject(self.part).thinnest_wall()
        if thinnest is None:
            return None, "wall thickness not measurable: the mesh is not closed"
        return thinnest >= self.min, f"thinnest wall {thinnest:.2f} mm (needs ≥ {self.min:g})"


class BodiesCheck(_OnPart):
    """The number of separate pieces."""

    kind: Literal["bodies"]
    count: int = Field(ge=1)
    example = {"kind": "bodies", "count": 1}

    def verify(self, build: Build) -> Verdict:
        bodies = build.subject(self.part).metrics.bodies
        return bodies == self.count, f"{bodies} (needs {self.count})"


class ClearanceCheck(_Bounded, _Check):
    """The gap between two assembly parts, mm; parts that overlap have no gap and fail any minimum."""

    kind: Literal["clearance"]
    between: tuple[str, str]
    example = {"kind": "clearance", "between": ["lid", "base"], "min": 0.3}

    def verify(self, build: Build) -> Verdict:
        a, b = (build.subject(name) for name in self.between)
        try:
            shared = a.overlap(b)
            if shared > OVERLAP_MM3:  # no gap at all: fails any minimum, even 0
                return self.min is None, f"{a.name} and {b.name} overlap by {shared:.1f} mm³"
            passed, detail = _bounds(a.gap(b), self.min, self.max, "mm")
        except ValueError as e:  # a part's mesh is not closed
            return None, f"not measurable: {e}"
        return passed, f"{a.name} to {b.name}: {detail}"


RequirementCheck = Annotated[
    SizeCheck | FitsWithinCheck | VolumeCheck | MassCheck | HoleCheck | WallCheck | BodiesCheck | ClearanceCheck,
    Field(discriminator="kind"),
]
REQUIREMENT_CHECKS: tuple[type[_Check], ...] = members(RequirementCheck)


class RequirementResult(Model):
    text: str
    status: Literal["pass", "fail", "unverified"]
    detail: str = ""


class Requirement(Model):
    """Something the design must satisfy. With a `check`, every build verifies it; without one, the human judges."""

    text: str
    check: RequirementCheck | None = None

    def verify(self, build: Build) -> RequirementResult:
        if self.check is None:
            return RequirementResult(text=self.text, status="unverified", detail="no check: the human judges it")
        try:
            passed, detail = self.check.verify(build)
        except LookupError as e:  # names a part the build does not have
            passed, detail = False, str(e)
        status = "unverified" if passed is None else "pass" if passed else "fail"
        return RequirementResult(text=self.text, status=status, detail=detail)


# --------------------------------------------------------------------------- manufacturing


class _Process(Model):
    """How a design will be made. Every build is checked for it, each part of an assembly on its own."""

    example: ClassVar[dict[str, Any]]

    @abstractmethod
    def checks(self, part: Part) -> list[Check]: ...


class FdmProcess(_Process):
    """FDM (filament) 3D printing, printed the way the model stands (+Z up, resting on its lowest Z): wall
    thickness, overhangs and bed contact. Optional `nozzle_mm`, `min_wall_mm`, `max_overhang_deg`."""

    process: Literal["fdm"]
    nozzle_mm: float = Field(default=0.4, gt=0)
    min_wall_mm: float = Field(default=0.8, gt=0, description="Thinnest wall that prints solid: two perimeters")
    max_overhang_deg: float = Field(
        default=45, ge=0, lt=90, description="Steepest overhang, from vertical, without support"
    )
    example = {"process": "fdm"}

    def checks(self, part: Part) -> list[Check]:
        from hitlcad.analysis.printability import fdm_checks

        return fdm_checks(part.mesh, self)


Manufacturing = Annotated[FdmProcess, Field(discriminator="process")]  # add processes to this union
MANUFACTURING_PROCESSES: tuple[type[_Process], ...] = members(Manufacturing)


# --------------------------------------------------------------------------- builds


class BuildStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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


class PartInfo(Model):
    """One part of an assembly (a design whose build(p) returns {name: shape}), measured on its own mesh."""

    name: str
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    size: tuple[float, float, float]
    volume: float | None
    bodies: int


class Artifact(Model):
    """A file produced for a revision; ``path`` is relative to the revision directory.

    Roles a backend produces: ``mesh`` (mesh.stl, every backend), ``exchange`` (the B-rep as STEP),
    ``native`` (the kernel's own file, e.g. .blend), and ``part_mesh`` / ``part_exchange`` per assembly part.
    """

    name: str
    path: str
    media_type: str
    role: Literal[
        "source",
        "params",
        "native",
        "mesh",
        "view",
        "exchange",
        "part_mesh",
        "part_exchange",
        "objects",
        "preview",
        "log",
        "report",
    ]
    sha256: str
    bytes: int


class BuildReport(Model):
    status: BuildStatus
    backend: str
    duration_s: float
    error: str | None = None
    log_tail: str = ""
    metrics: MeshMetrics | None = None
    checks: list[Check] = Field(default_factory=list)
    parts: list[PartInfo] = Field(default_factory=list, description="Empty unless the design is an assembly")
    requirements: list[RequirementResult] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)

    def artifact(self, role: str) -> Artifact | None:
        return next((a for a in self.artifacts if a.role == role), None)


class Check(Model):
    name: str
    passed: bool
    detail: str = ""
    severity: Literal["error", "warning", "info"] = "error"


BuildReport.model_rebuild()


class RevisionOrigin(StrEnum):
    FILE = "file"  # built from the design folder's files
    PARAMETER_EDIT = "parameter_edit"  # the human changed parameters in the panel


class BuildSpec(Model):
    """Everything a build depends on: the model, and what it is checked and verified against."""

    backend: str
    source: str
    parameters: list[ParamSpec] = Field(default_factory=list)
    manufacturing: Manufacturing | None = None
    requirements: list[Requirement] = Field(default_factory=list)

    @property
    def key(self) -> str:
        """Hash of the spec: a build whose key matches the last one's would build the same."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class Revision(Model):
    id: int
    created_at: datetime = Field(default_factory=utcnow)
    origin: RevisionOrigin
    parent: int | None = None
    spec: BuildSpec
    summary: str = ""
    report: BuildReport


# --------------------------------------------------------------------------- selection and pins

Vec = tuple[float, float, float]


class ComponentRef(Model):
    """A B-rep face, edge or vertex as a 3D view picked it, `index` into that part's build123d
    `faces()` / `edges()` / `vertices()`; or the whole part (`solid`, index 0)."""

    part: str = Field(description="Viewer part path, e.g. '/asm/block'")
    topo: Literal["face", "edge", "vertex", "solid"]
    index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _one_solid_per_part(self) -> ComponentRef:
        if self.topo == "solid" and self.index != 0:
            raise ValueError("a solid is the whole part: index 0")
        return self


class Pick(Model):
    """A click in a 3D view: the surface point, and on STEP models the face, edge, vertex or part it hit."""

    point: Vec = Field(description="Model-space point in mm, Z up")
    normal: Vec | None = None
    component: ComponentRef | None = None


class SelectionItem(Model):
    """Something the human selected in the 3D view, described for agents."""

    id: str = Field(default_factory=lambda: new_id("sel"))
    revision: int | None = None
    point: Vec = Field(description="Model-space point in mm, Z up")
    normal: Vec | None = None
    kind: Literal["face", "edge", "vertex", "solid", "point"] = "point"
    label: str = Field(description="Human-readable description, e.g. 'Cylinder face r=2.25 mm …'")
    details: dict[str, Any] = Field(default_factory=dict)
    component: ComponentRef | None = None


class Pin(Model):
    """A note the human pinned to the model surface. Open until the agent or the human resolves it."""

    id: str = Field(default_factory=lambda: new_id("pin"))
    revision: int | None = Field(default=None, description="Revision the pin was placed on")
    point: Vec = Field(description="Model-space point in mm, Z up")
    normal: Vec | None = None
    component: ComponentRef | None = Field(default=None, description="The face, edge, vertex or part it is on (STEP)")
    note: str = ""
    label: str = Field(default="", description="What is under the pin, e.g. 'Planar face 40 × 30 mm'")
    created_at: datetime = Field(default_factory=utcnow)
    resolved: bool = False
    reply: str = Field(default="", description="How the agent addressed it, set when resolving")
