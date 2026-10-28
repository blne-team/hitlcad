"""What design.json declares about a product's structure, but for its parts (`core.design`): what a connector
kind, a mate type and a relation type are (`Connector`, `Mate`, `Relation`), components, fits, fastener sets,
and the tolerances every check uses.

The kinds and types themselves are plugins' members (hitlcad's own are in `hitlcad.builtin`): each carries what
it means (fields, docstring, example, verification), and design.json's unions are made of those installed. The
framework knows a member only through what its base class here declares, never by its name: a round feature is
one whose `round` says so.
"""

from __future__ import annotations

import math
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple

import numpy as np
from pydantic import Field, field_validator, model_validator

from hitlcad.core import frames as fr
from hitlcad.core import iso286
from hitlcad.core.address import End
from hitlcad.kernel.model import Model

if TYPE_CHECKING:
    from hitlcad.core.laws import Law
    from hitlcad.geometry.features import Features

Ref = str  # a parameter name, or "interfaces.<name>" for an interface value
Symmetry = int | Literal["axial"] | None


class Tolerances(Model):
    """Resolved verification settings: the design's own, over hitlcad's settings."""

    position_mm: float
    angle_deg: float
    motion_steps: int


class Verification(Model):
    """Tolerances and sampling every check uses. Unset fields take hitlcad's settings (`HITLCAD_VERIFY_*`)."""

    position_mm: float | None = Field(default=None, gt=0, description="Positions agree within this distance")
    angle_deg: float | None = Field(default=None, gt=0, description="Directions agree within this angle")
    motion_steps: int | None = Field(
        default=None, ge=10, description="Most positions motion analysis checks per mate before it gives up"
    )

    def resolve(self, defaults: Tolerances) -> Tolerances:
        return defaults.model_copy(update=self.model_dump(exclude_none=True))


# --------------------------------------------------------------------------- connectors


class Measured(Model):
    """A connector frame's feature, as measured on the part."""

    status: Literal["pass", "fail", "unverified"]
    detail: str
    values: dict[str, float] = Field(default_factory=dict)
    uncertainty_mm: float = Field(default=0.0, description="Either way on the values in mm: a mesh's facets")


class Connector(Model):
    """A connector kind: a named frame (or pattern of frames) on a part, at a feature another part meets."""

    count: int | str = Field(default=1, description="Frames in the pattern: a number or a parameter name")
    symmetry: Symmetry = Field(
        default=None, description="Rotations about Z that look the same: n (every 360°/n) or 'axial'"
    )
    description: str = ""

    example: ClassVar[dict[str, Any]]
    #: Whether its frames have a feature to measure; a datum has none, so it has no result of its own.
    measures: ClassVar[bool] = True
    #: The round feature it is, if one: a fit is between a "hole" and a "shaft", whatever their kinds.
    round: ClassVar[Literal["hole", "shaft"] | None] = None

    def nominal_refs(self) -> dict[str, Ref]:
        """Nominal values the kind takes from parameters or interface values, by field."""
        return {}

    def fixed_nominals(self) -> dict[str, float]:
        """Nominal values it states itself, by name (a gear's `internal`): what its relations read with the rest."""
        return {}

    @abstractmethod
    def measure(self, features: Features, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        """The feature at `frame` on the part."""

    def owns(self, item: Any, frame: fr.Frame, tol: Tolerances) -> bool:
        """Whether a face of the part (in its own frame) is this connector's feature at `frame`: what a pick on it
        is named after."""
        return False


def symmetry_of(a: Symmetry, b: Symmetry) -> int | Literal["axial"]:
    """How two mated features may turn against each other unnoticed: the symmetry both share."""
    if a == "axial":
        return b or 1 if b != "axial" else "axial"
    if b == "axial":
        return a or 1
    return math.gcd(a or 1, b or 1)


# --------------------------------------------------------------------------- parts and components


class Catalog(Model):
    """A bought part: what to buy, and how faithful its geometry is."""

    designation: str = Field(description="What to buy, e.g. 'ISO 4762 M3 × 14'")
    fidelity: Literal["exact", "simplified"] = Field(
        description="exact: the manufacturer's geometry; simplified: an envelope, so fits against it are approximate"
    )
    supplier: str = ""
    mpn: str = Field(default="", description="Manufacturer part number")


class ComponentSpec(Model):
    design: str = Field(description="The component's design folder, relative to this one, e.g. ../gearbox")
    configuration: str | None = Field(default=None, description="One of the component's configurations")
    bind: dict[str, Ref] = Field(
        default_factory=dict, description="Component parameter -> this design's parameter or interface value"
    )
    flexible: bool = Field(default=False, description="Its internal motion takes part in this design's motion analysis")


# --------------------------------------------------------------------------- mates


class Fit(Model):
    """How a round shaft sits in a round hole: a diametral clearance range in mm (negative is interference), or
    an ISO 286 fit: its hole and shaft are modelled at one basic size, and its classes' limits there, reported
    with the fit, go on the drawing. A mate that moves needs a fit that leaves room to move."""

    clearance: tuple[float, float] | None = None
    iso: str | None = Field(default=None, description="e.g. 'H7/g6'")

    @field_validator("iso")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        if v is not None:
            iso286.parse(v)
        return v

    @model_validator(mode="after")
    def _one(self) -> Fit:
        if (self.clearance is None) == (self.iso is None):
            raise ValueError("give a fit as clearance [min, max] or as iso, e.g. 'H7/g6'")
        if self.clearance and self.clearance[0] > self.clearance[1]:
            raise ValueError("fit clearance min is greater than max")
        return self

    def limits(self, nominal_mm: float) -> tuple[float, float]:
        """(least, greatest) diametral clearance in mm; `ValueError` where ISO 286 does not define the fit at this
        size."""
        if self.clearance:
            return self.clearance
        return iso286.fit_clearance(self.iso, nominal_mm)  # type: ignore[arg-type]  # one of the two is set

    def allows_interference(self, nominal_mm: float) -> bool:
        """Whether the fit lets the parts overlap; not where ISO 286 does not define it (the mate's fit fails)."""
        try:
            return self.limits(nominal_mm)[0] < 0
        except ValueError:
            return False


class Limits(Model):
    angle: tuple[float, float] | None = Field(default=None, description="Degrees")
    travel: tuple[float, float] | None = Field(default=None, description="mm")


class MateCheck(Model):
    """What a mate's frames say after placement."""

    passed: bool
    detail: str
    values: dict[str, float] = Field(default_factory=dict)
    free: dict[str, float] = Field(default_factory=dict, description="Position along each free motion: angle, travel")


class Mate(Model):
    """A mate type: two connector ends held together, with the motion it lets b make against a."""

    a: str = Field(description="Connector end, e.g. 'lid.bottom'")
    b: str
    fit: Fit | None = None
    overlap: bool = Field(
        default=False,
        description="b's part is set into a hole of a's part and overlaps its wall: a heat-set insert's "
        "knurls, a modelled thread, a pressed pin; a's part has no material on the hole's axis where b sits",
    )
    flip: bool = Field(
        default=False,
        description="b's Z runs with a's instead of facing it: a shaft entering a bore "
        "from the end opposite the bore's declared opening",
    )

    example: ClassVar[dict[str, Any]]
    free_motion: ClassVar[tuple[str, ...]] = ()  # "angle", "travel": what sampling moves along
    #: What of b's frame, seen in a's, stays as assembled while it moves: "origin_xy" and "origin_z" (b's
    #: origin across and along a's Z), "axis" (b's Z) and "clocking" (b's X about it, up to the connectors'
    #: symmetry). A block solver holds these; what is not named moves freely.
    holds: ClassVar[frozenset[str]]

    @field_validator("a", "b")
    @classmethod
    def _end(cls, v: str) -> str:
        End.parse(v)
        return v

    @property
    def address(self) -> str:
        return f"{self.a}+{self.b}"

    def facing(self, fb: fr.Frame) -> fr.Frame:
        """b's frame as the mate compares it: turned to face a when the mate is flipped."""
        return fb @ fr.flip_x() if self.flip else fb

    @abstractmethod
    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        """`fb` already as `facing` gives it."""

    def limits_of(self) -> Limits | None:
        return getattr(self, "limits", None)

    def motion(self, fa: fr.Frame, kind: str, value: float) -> fr.Frame:
        """The rigid motion of the b side when it moves `value` along its free motion `kind`."""
        if kind == "angle":
            return fr.rotation_about(fr.origin(fa), fr.z_axis(fa), value)
        return fr.translation(fr.z_axis(fa) * value)


# --------------------------------------------------------------------------- relations


class RelationCheck(Model):
    passed: bool | None
    detail: str
    values: dict[str, float] = Field(default_factory=dict)


class Axis(NamedTuple):
    """A coordinate a relation couples: a turn (degrees) about, or a travel (mm) along, `direction` through
    `origin`."""

    kind: Literal["angle", "travel"]
    origin: np.ndarray
    direction: np.ndarray

    def motion(self, value: float) -> fr.Frame:
        """The rigid motion that moves `value` along this coordinate."""
        if self.kind == "angle":
            return fr.rotation_about(self.origin, self.direction, value)
        return fr.translation(self.direction * value)

    def of(self, motion: fr.Frame) -> float:
        """How far a rigid motion moves along this coordinate."""
        if self.kind == "angle":
            u = np.cross(self.direction, [1.0, 0.0, 0.0])
            u = u if np.linalg.norm(u) > 1e-6 else np.cross(self.direction, [0.0, 1.0, 0.0])
            return fr.signed_angle_deg(u, motion[:3, :3] @ u, self.direction)
        return float(np.dot(motion[:3, :3] @ self.origin + motion[:3, 3] - self.origin, self.direction))


@dataclass(frozen=True)
class Ends:
    """A relation's two ends as assembled: their placed frames, their connectors' nominal values, and what its design
    names (`value`: a parameter or interface value by reference, e.g. 'interfaces.cam_lift')."""

    fa: fr.Frame
    fb: fr.Frame
    na: dict[str, float]
    nb: dict[str, float]
    value: Callable[[str], Any]


class Relation(Model):
    """A relation type: motion coupled between two connectors (gears, a rack, a lead screw, a cam)."""

    a: str
    b: str
    tolerance: float | None = Field(default=None, gt=0, description="mm; default: verification.position_mm")

    example: ClassVar[dict[str, Any]]
    joins: ClassVar[tuple[str, str]]  # connector kinds of a and b
    #: Its ends' parts stay in contact as they move (a cam and its follower): motion checks, at each position it
    #: checks, that they neither part nor overlap, within the relation's tolerance and the meshes'.
    touching: ClassVar[bool] = False

    @field_validator("a", "b")
    @classmethod
    def _end(cls, v: str) -> str:
        End.parse(v)
        return v

    @property
    def address(self) -> str:
        return f"{self.a}~{self.b}"

    @abstractmethod
    def verify(self, ends: Ends, tol: Any) -> RelationCheck: ...

    @abstractmethod
    def axes(self, fa: fr.Frame, fb: fr.Frame) -> tuple[Axis, Axis]:
        """The coordinates of a and b the relation couples, from the placed frames."""

    @abstractmethod
    def law(self, ends: Ends) -> Law:
        """How far b moves along its axis as a moves along its own, both from as assembled (degrees or mm). A law
        that is `one_way` drives b from a only."""

    @abstractmethod
    def coordinates(self, fa0: fr.Frame, fb0: fr.Frame, fa: Any, fb: Any) -> tuple[Any, Any]:
        """How far a and b have moved along the coordinates the relation couples, from where the frames were
        (fa0, fb0) to where they are (fa, fb), each measured against what holds the coupling (the line of the
        gears' centres, the rack, the screw, the follower's line): b's = `law`(a's) while the relation holds. A turn
        comes as degrees in (-180, 180], a travel as mm; their kinds are those of `axes`. fa0 and fb0 are numbers;
        fa and fb numbers, or a loop solver's symbols: measure them with `frames.heading`, `frames.coordinate` and
        `frames.wrapped`, slicing and arithmetic, so the solver compiles them."""


# --------------------------------------------------------------------------- fastener sets


class FastenerSet(Model):
    """A catalog fastener through clearance-hole patterns (head side first) into a threaded or insert hole.
    `fastener` names the fastener's seat connector (under the head, Z along the shank); the fastener part's
    catalog provider must give its `thread`, `thread_diameter`, `thread_pitch`, `length` and its ISO 273
    clearance holes `clearance_fine` and `clearance_medium`. Verified per stack: the holes share an axis, the
    fastener is seated, its shank clears every through hole (ISO 273 fine, after the process allowance), its
    thread is the into thread's (a catalog insert's or nut's size; a made hole between 0.7 and 1 × its
    diameter), it engages the material minimum and the space along its axis past the into hole is free."""

    fastener: str = Field(description="The fastener's seat connector, e.g. 'lid_screw.seat'")
    through: list[str] = Field(min_length=1, description="Clearance-hole connectors, head side first")
    into: str = Field(description="The threaded or insert hole connector it ends in")
    engagement: float | None = Field(
        default=None,
        gt=0,
        description="Minimum thread engagement in nominal diameters; default: the into part's material",
    )
    example: ClassVar[dict[str, Any]] = {
        "fastener": "lid_screw.seat",
        "through": ["lid.screws"],
        "into": "insert.thread",
    }

    @field_validator("fastener", "into")
    @classmethod
    def _into(cls, v: str) -> str:
        End.parse(v)
        return v

    @field_validator("through")
    @classmethod
    def _through(cls, v: list[str]) -> list[str]:
        for end in v:
            End.parse(end)
        return v

    @property
    def address(self) -> str:
        return f"{self.fastener}:{'>'.join(self.through)}>{self.into}"
