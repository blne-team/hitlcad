"""Domain model shared by every layer: parameters, briefs, selection and pins.

design.json itself is `core.design`; what its connector kinds, mate and relation types are is `core.structure`,
what requirement checks and processes are `core.checks`, builds and their results `core.report`.
All lengths are millimetres. Z is up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from hitlcad.kernel.model import Model


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


Vec = tuple[float, float, float]
Value = float | int | bool | str
Matrix = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]  # a 4 × 4 homogeneous transform, row-major, mm


# --------------------------------------------------------------------------- parameters


class ParamSpec(Model):
    """A named, user-tunable input of a parametric model."""

    name: str = Field(description="Key in the params dict, e.g. wall_thickness")
    label: str = Field(default="", description="Human readable label")
    type: Literal["float", "int", "bool", "choice"] = "float"
    value: Value
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = Field(default="mm", description="Display unit, e.g. mm, deg, count")
    choices: list[str] | None = None
    description: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> ParamSpec:
        self.value = self.coerce(self.value)  # a float written as 30 is 30.0
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"parameter {self.name!r} has min > max")
        return self

    def coerce(self, raw: Any) -> Value:
        """Validate and coerce a value against this spec."""
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
        if self.type == "int" and not float(raw).is_integer():
            raise ValueError(f"{self.name}: expected a whole number, got {raw}")
        value: float | int = int(raw) if self.type == "int" else float(raw)
        if self.min is not None and value < self.min:
            raise ValueError(f"{self.name}: {value} is below minimum {self.min}")
        if self.max is not None and value > self.max:
            raise ValueError(f"{self.name}: {value} is above maximum {self.max}")
        return value


def apply_values(specs: list[ParamSpec], values: dict[str, Any]) -> list[ParamSpec]:
    """Copies of ``specs`` with validated ``values`` applied. Unknown keys are rejected."""
    by_name = {p.name: p for p in specs}
    if unknown := set(values) - set(by_name):
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
    key_dimensions: list[Dimension] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- selection and pins

Topo = Literal["face", "edge", "vertex", "solid"]


class ComponentRef(Model):
    """What a 3D view pick hit on a B-rep build: a face, edge or vertex of one instance, numbered as
    `faces()` / `edges()` / `vertices()` of that instance's part (`build/parts/<part>.step`), or
    the whole instance (`solid`, index 0)."""

    instance: str = Field(description="Instance address, e.g. 'lid' or 'tilt/planet[2]'")
    topo: Topo
    index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _one_solid_per_instance(self) -> ComponentRef:
        if self.topo == "solid" and self.index != 0:
            raise ValueError("a solid is the whole instance: index 0")
        return self


class Pick(Model):
    """A click in a 3D view: the surface point, and on B-rep builds the component it hit."""

    point: Vec = Field(description="Model-space point in mm, Z up, where the part it is on was assembled")
    normal: Vec | None = None
    component: ComponentRef | None = None


class SelectionItem(Model):
    """Something the human selected in the 3D view, described for agents."""

    id: str = Field(default_factory=lambda: new_id("sel"))
    revision: int | None = None
    point: Vec = Field(description="Model-space point in mm, Z up")
    normal: Vec | None = None
    kind: Literal["face", "edge", "vertex", "solid", "point"] = "point"
    label: str = Field(description="Human-readable description, in the address grammar")
    details: dict[str, Any] = Field(default_factory=dict)
    instance: str | None = Field(default=None, description="The instance picked, on builds")
    component: ComponentRef | None = None
    connector: str | None = Field(default=None, description="The connector whose feature was picked, e.g. 'lid.screws'")


class PinMotion(Model):
    """Where a range of motion was when a pin was placed: the pin is about the parts there, not as assembled."""

    pair: str = Field(description="The mate moved, by its frames, e.g. 'base.a ↔ crank.a'")
    kind: Literal["angle", "travel"]
    at: float = Field(description="Degrees or mm, as the mate counts them")

    def text(self) -> str:
        return f"with {self.pair} at {self.at:.1f}{'°' if self.kind == 'angle' else ' mm'}"


class Pin(Model):
    """A note the human pinned to the model surface. Open until the agent or the human resolves it."""

    id: str = Field(default_factory=lambda: new_id("pin"))
    revision: int | None = Field(default=None, description="Revision the pin was placed on")
    point: Vec = Field(description="Model-space point in mm, Z up")
    normal: Vec | None = None
    instance: str | None = Field(default=None, description="The instance it is on")
    component: ComponentRef | None = Field(default=None, description="The instance's component it is on (B-rep)")
    connector: str | None = Field(default=None, description="The connector whose feature it is on")
    note: str = ""
    motion: PinMotion | None = Field(default=None, description="Where a range of motion was; none: as assembled")
    label: str = Field(default="", description="What is under the pin")
    created_at: datetime = Field(default_factory=utcnow)
    resolved: bool = False
    reply: str = Field(default="", description="How the agent addressed it, set when resolving")


class WatchedMotion(Model):
    """A loop's motion the human watched move in the panel: its sweep (`MotionRange.key`), which the review is of,
    so that any change to the loop or what it moves past leaves it unwatched."""

    key: str
    pair: str = Field(description="The mate that drives the loop, e.g. 'base.a ↔ crank.a'")
    revision: int = Field(description="Revision it was watched on")
    created_at: datetime = Field(default_factory=utcnow)
