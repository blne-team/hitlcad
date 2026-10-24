"""What design.json declares about a product's structure: parts and their connectors, components,
mates, relations and fastener sets, and the tolerances every check uses.

Connector kinds, mate types and relation types are unions: each member carries what it means
(fields, docstring, example, verification). The framework knows them only through these unions,
so a new kind of connector or mate is one class here and nothing anywhere else.
"""

from __future__ import annotations

import math
from abc import abstractmethod
from typing import Annotated, Any, ClassVar, Literal, NamedTuple

import numpy as np
from pydantic import Field, field_validator, model_validator

from hitlcad.core import frames as fr
from hitlcad.core.address import End, check_name, is_interface_ref
from hitlcad.core.checks import Manufacturing
from hitlcad.core.types import Model, members

Ref = str  # a parameter name, or "interfaces.<name>" for an interface value
Symmetry = int | Literal["axial"] | None


class Tolerances(Model):
    """Resolved verification settings: the design's own, over hitlcad's settings."""

    position_mm: float
    angle_deg: float
    motion_samples: int


class Verification(Model):
    """Tolerances and sampling every check uses. Unset fields take hitlcad's settings (`HITLCAD_VERIFY_*`)."""

    position_mm: float | None = Field(default=None, gt=0, description="Positions agree within this distance")
    angle_deg: float | None = Field(default=None, gt=0, description="Directions agree within this angle")
    motion_samples: int | None = Field(default=None, ge=2, description="Positions sampled per range of motion")

    def resolve(self, defaults: Tolerances) -> Tolerances:
        return defaults.model_copy(update=self.model_dump(exclude_none=True))


# --------------------------------------------------------------------------- connectors


class Measured(Model):
    """A connector frame's feature, as measured on the part."""

    status: Literal["pass", "fail", "unverified"]
    detail: str
    values: dict[str, float] = Field(default_factory=dict)


class _Connector(Model):
    count: int | str = Field(default=1, description="Frames in the pattern: a number or a parameter name")
    symmetry: Symmetry = Field(
        default=None, description="Rotations about Z that look the same: n (every 360°/n) or 'axial'"
    )
    description: str = ""

    example: ClassVar[dict[str, Any]]

    def nominal_refs(self) -> dict[str, Ref]:
        """Nominal values the kind takes from parameters or interface values, by field."""
        return {}

    @abstractmethod
    def measure(self, shape: Any | None, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        """The feature at `frame` on the part's B-rep (`shape`; None for a mesh part)."""


def _needs_brep(shape: Any | None, what: str) -> Measured | None:
    return (
        None
        if shape is not None
        else Measured(status="unverified", detail=f"{what} is measured on B-rep parts; this part is a mesh")
    )


class FrameConnector(_Connector):
    """A datum: a position and orientation with no feature to measure (an axis, a reference point)."""

    kind: Literal["frame"]
    example = {"kind": "frame", "symmetry": "axial"}

    def measure(self, shape: Any | None, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        return Measured(status="pass", detail="datum")


class FaceConnector(_Connector):
    """A planar face. Origin on its plane, inside its outline (the centre of a rim is fine), Z its outward
    normal. Measured: the plane and its area."""

    kind: Literal["face"]
    example = {"kind": "face", "symmetry": 4}

    def measure(self, shape: Any | None, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        from hitlcad.analysis.connectors import measure_face

        return _needs_brep(shape, "a face") or measure_face(shape, frame, tol)


class HoleConnector(_Connector):
    """A round hole. Origin at its opening on the axis, Z out of the opening. Measured: the diameter and
    depth of the hole adjacent to the origin, below it."""

    kind: Literal["hole"]
    example = {"kind": "hole", "count": 4}

    def measure(self, shape: Any | None, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        from hitlcad.analysis.connectors import measure_round

        return _needs_brep(shape, "a hole") or measure_round(shape, frame, tol, inward=True)


class ShaftConnector(_Connector):
    """A round shaft or pin. Origin at its shoulder on the axis, Z towards its tip. Measured: the
    diameter and length of the shaft adjacent to the origin, along Z."""

    kind: Literal["shaft"]
    example = {"kind": "shaft"}

    def measure(self, shape: Any | None, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        from hitlcad.analysis.connectors import measure_round

        return _needs_brep(shape, "a shaft") or measure_round(shape, frame, tol, inward=False)


class GearConnector(_Connector):
    """A spur gear. Origin at its centre at the bottom of the teeth, Z along its axis. Tooth geometry is
    verified by its relations and by interference; the nominal values name parameters or interface values."""

    kind: Literal["gear"]
    teeth: Ref
    module: Ref
    width: Ref
    internal: bool = False
    example = {"kind": "gear", "teeth": "sun_teeth", "module": "module", "width": "face_width"}

    def nominal_refs(self) -> dict[str, Ref]:
        return {"teeth": self.teeth, "module": self.module, "width": self.width}

    def measure(self, shape: Any | None, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        kind = "internal" if self.internal else "external"
        return Measured(status="pass", detail=f"{kind} gear m{nominals['module']:g}, {nominals['teeth']:g} teeth, "
                        f"{nominals['width']:g} mm wide (nominal)", values=nominals)  # fmt: skip


ConnectorSpec = Annotated[
    FrameConnector | FaceConnector | HoleConnector | ShaftConnector | GearConnector, Field(discriminator="kind")
]
CONNECTOR_KINDS: tuple[type[_Connector], ...] = members(ConnectorSpec)
ROUND_KINDS = frozenset({"hole", "shaft"})


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


class PartSpec(Model):
    role: str = Field(description="One line: what the part does")
    file: str = Field(description="The part file that builds it, e.g. parts/lid.py")
    number: str = Field(default="", description="The user's own part number")
    material: str | None = Field(default=None, description="A name in the material registry")
    manufacturing: Manufacturing | None = Field(
        default=None, description="Overrides the design's process for this made part"
    )
    backend: str | None = Field(default=None, description="Overrides the design's backend")
    catalog: Catalog | None = None
    connectors: dict[str, ConnectorSpec] = Field(default_factory=dict)

    @field_validator("file")
    @classmethod
    def _in_parts(cls, v: str) -> str:
        if not (v.startswith("parts/") and v.endswith(".py")) or ".." in v:
            raise ValueError(f"part file {v!r} must be a .py file under parts/")
        return v

    @field_validator("connectors")
    @classmethod
    def _names(cls, v: dict[str, Any]) -> dict[str, Any]:
        for name in v:
            check_name(name, "connector")
        return v

    @model_validator(mode="after")
    def _bought_or_made(self) -> PartSpec:
        if self.catalog and self.manufacturing:
            raise ValueError("a catalog part is bought, not manufactured: drop its manufacturing")
        return self


class ComponentSpec(Model):
    design: str = Field(description="The component's design folder, relative to this one, e.g. ../gearbox")
    configuration: str | None = Field(default=None, description="One of the component's configurations")
    bind: dict[str, Ref] = Field(
        default_factory=dict, description="Component parameter -> this design's parameter or interface value"
    )
    flexible: bool = Field(default=False, description="Its internal motion takes part in this design's motion analysis")


# --------------------------------------------------------------------------- mates


class Fit(Model):
    """How a round shaft sits in a round hole: a diametral clearance range in mm (negative is
    interference), or an ISO 286 fit evaluated at the measured nominal size."""

    clearance: tuple[float, float] | None = None
    iso: str | None = Field(default=None, description="e.g. 'H7/g6'")

    @model_validator(mode="after")
    def _one(self) -> Fit:
        if (self.clearance is None) == (self.iso is None):
            raise ValueError("give a fit as clearance [min, max] or as iso, e.g. 'H7/g6'")
        if self.clearance and self.clearance[0] > self.clearance[1]:
            raise ValueError("fit clearance min is greater than max")
        return self

    def limits(self, nominal_mm: float) -> tuple[float, float]:
        if self.clearance:
            return self.clearance
        from hitlcad.analysis.iso286 import fit_clearance

        assert self.iso is not None
        return fit_clearance(self.iso, nominal_mm)

    def allows_interference(self, nominal_mm: float) -> bool:
        return self.limits(nominal_mm)[0] < 0


class Limits(Model):
    angle: tuple[float, float] | None = Field(default=None, description="Degrees")
    travel: tuple[float, float] | None = Field(default=None, description="mm")


class MateCheck(Model):
    """What a mate's frames say after placement."""

    passed: bool
    detail: str
    values: dict[str, float] = Field(default_factory=dict)
    free: dict[str, float] = Field(default_factory=dict, description="Position along each free motion: angle, travel")


class _Mate(Model):
    a: str = Field(description="Connector end, e.g. 'lid.bottom'")
    b: str
    fit: Fit | None = None
    overlap: bool = Field(
        default=False,
        description="The two parts are meant to overlap here: a heat-set insert's "
        "knurls, a modelled thread, a press fit between frames",
    )
    flip: bool = Field(
        default=False,
        description="b's Z runs with a's instead of facing it: a shaft entering a bore "
        "from the end opposite the bore's declared opening",
    )

    example: ClassVar[dict[str, Any]]
    free_motion: ClassVar[tuple[str, ...]] = ()  # "angle", "travel": what sampling moves along

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


def _opposed(fa: fr.Frame, fb: fr.Frame) -> float:
    return fr.angle_deg(fr.z_axis(fa), -fr.z_axis(fb))


def _clocking(fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"]) -> float:
    angle = fr.signed_angle_deg(fr.x_axis(fa), fr.x_axis(fb), fr.z_axis(fa))
    return 0.0 if symmetry == "axial" else fr.reduce_angle(angle, symmetry)


def _summary(values: dict[str, float], tol: Any) -> tuple[bool, str]:
    worst = []
    ok = True
    for key, v in values.items():
        limit = tol.angle_deg if key.endswith("_deg") else tol.position_mm
        if abs(v) > limit:
            ok = False
            worst.append(f"{key.removesuffix('_deg').removesuffix('_mm').replace('_', ' ')} "
                         f"{abs(v):.3f}{'°' if key.endswith('_deg') else ' mm'}")  # fmt: skip
    return ok, "frames agree" if ok else "off by " + ", ".join(worst)


class RigidMate(_Mate):
    """Nothing moves: origins coincide (Z apart by `offset`), Z axes face each other, X axes agree up to
    the features' shared symmetry."""

    type: Literal["rigid"]
    offset: float = Field(default=0.0, description="Designed gap along a's Z, mm")
    example = {"type": "rigid", "a": "lid.bottom", "b": "housing.top"}

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        target = fr.origin(fa) + fr.z_axis(fa) * self.offset
        values = {"origin_mm": float(np.linalg.norm(fr.origin(fb) - target)), "axis_deg": _opposed(fa, fb),
                  "clocking_deg": _clocking(fa, fb, symmetry)}  # fmt: skip
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values)


class RevoluteMate(_Mate):
    """Turns about Z: origins coincide (Z apart by `offset`), Z axes face each other."""

    type: Literal["revolute"]
    offset: float = 0.0
    limits: Limits | None = None
    example = {"type": "revolute", "a": "carrier.pins", "b": "planet.bore", "fit": {"clearance": [0.2, 0.5]}}
    free_motion = ("angle",)

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        target = fr.origin(fa) + fr.z_axis(fa) * self.offset
        values = {"origin_mm": float(np.linalg.norm(fr.origin(fb) - target)), "axis_deg": _opposed(fa, fb)}
        angle = fr.signed_angle_deg(fr.x_axis(fa), fr.x_axis(fb), fr.z_axis(fa))
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values, free={"angle": angle})


class SliderMate(_Mate):
    """Slides along Z: origins on one line along Z, Z axes face each other, X axes agree up to symmetry."""

    type: Literal["slider"]
    limits: Limits | None = None
    example = {"type": "slider", "a": "rail.track", "b": "carriage.runner", "limits": {"travel": [0, 120]}}
    free_motion = ("travel",)

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        oa, za = fr.origin(fa), fr.z_axis(fa)
        values = {"axis_offset_mm": fr.distance_to_line(fr.origin(fb), oa, za), "axis_deg": _opposed(fa, fb),
                  "clocking_deg": _clocking(fa, fb, symmetry)}  # fmt: skip
        free = {"travel": fr.along(fr.origin(fb), oa, za)}
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values, free=free)


class CylindricalMate(_Mate):
    """Turns about and slides along Z: origins on one line along Z, Z axes face each other."""

    type: Literal["cylindrical"]
    limits: Limits | None = None
    example = {"type": "cylindrical", "a": "motor.shaft", "b": "sun.bore", "fit": {"clearance": [0.0, 0.1]}}
    free_motion = ("angle", "travel")

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        oa, za = fr.origin(fa), fr.z_axis(fa)
        values = {"axis_offset_mm": fr.distance_to_line(fr.origin(fb), oa, za), "axis_deg": _opposed(fa, fb)}
        free = {"travel": fr.along(fr.origin(fb), oa, za),
                "angle": fr.signed_angle_deg(fr.x_axis(fa), fr.x_axis(fb), za)}  # fmt: skip
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values, free=free)


class PlanarMate(_Mate):
    """Slides in the plane and turns about Z: b's origin on a's plane (apart by `offset`), Z axes face each other."""

    type: Literal["planar"]
    offset: float = 0.0
    example = {"type": "planar", "a": "table.top", "b": "box.bottom"}

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        gap = fr.along(fr.origin(fb), fr.origin(fa), fr.z_axis(fa)) - self.offset
        values = {"plane_mm": gap, "axis_deg": _opposed(fa, fb)}
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values)


class BallMate(_Mate):
    """Turns about its origin: the origins coincide."""

    type: Literal["ball"]
    example = {"type": "ball", "a": "arm.socket", "b": "rod.ball"}

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        values = {"origin_mm": float(np.linalg.norm(fr.origin(fb) - fr.origin(fa)))}
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values)


MateSpec = Annotated[
    RigidMate | RevoluteMate | SliderMate | CylindricalMate | PlanarMate | BallMate, Field(discriminator="type")
]
MATE_TYPES: tuple[type[_Mate], ...] = members(MateSpec)


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


class _Relation(Model):
    a: str
    b: str
    tolerance: float | None = Field(default=None, gt=0, description="mm; default: verification.position_mm")

    example: ClassVar[dict[str, Any]]
    joins: ClassVar[tuple[str, str]]  # connector kinds of a and b

    @field_validator("a", "b")
    @classmethod
    def _end(cls, v: str) -> str:
        End.parse(v)
        return v

    @property
    def address(self) -> str:
        return f"{self.a}~{self.b}"

    @abstractmethod
    def verify(self, fa: fr.Frame, fb: fr.Frame, na: dict[str, float], nb: dict[str, float],
               tol: Any) -> RelationCheck: ...  # fmt: skip

    @abstractmethod
    def axes(self, fa: fr.Frame, fb: fr.Frame) -> tuple[Axis, Axis]:
        """The coordinates of a and b the relation couples, from the placed frames."""

    @abstractmethod
    def ratio(self, na: dict[str, float], nb: dict[str, float]) -> float:
        """How far b moves along its axis per unit a moves along its own (degrees per degree, mm per degree)."""


class GearRelation(_Relation):
    """Two spur gears in mesh: same module, parallel axes, centre distance m(z₁ ± z₂)/2 (minus when one is
    internal), overlapping face widths. b turns −z_a/z_b times a (+ for an internal gear)."""

    type: Literal["gear"]
    example = {"type": "gear", "a": "sun.teeth", "b": "planet.teeth"}
    joins = ("gear", "gear")

    def verify(self, fa: fr.Frame, fb: fr.Frame, na: dict[str, float], nb: dict[str, float], tol: Any) -> RelationCheck:
        limit = self.tolerance or tol.position_mm
        if not math.isclose(na["module"], nb["module"]):
            return RelationCheck(passed=False, detail=f"modules differ: {na['module']:g} and {nb['module']:g}")
        m, za, zb = na["module"], na["teeth"], nb["teeth"]
        internal = bool(na.get("internal") or nb.get("internal"))
        expected = m * (abs(za - zb) if internal else za + zb) / 2
        oa, axis = fr.origin(fa), fr.z_axis(fa)
        actual = fr.distance_to_line(fr.origin(fb), oa, axis)
        tilt = min(fr.angle_deg(axis, fr.z_axis(fb)), fr.angle_deg(axis, -fr.z_axis(fb)))
        ta, tb = fr.along(oa, oa, axis), fr.along(fr.origin(fb), oa, axis)
        sign = 1.0 if np.dot(axis, fr.z_axis(fb)) >= 0 else -1.0
        span_a, span_b = (ta, ta + na["width"]), sorted((tb, tb + sign * nb["width"]))
        overlap = min(span_a[1], span_b[1]) - max(span_a[0], span_b[0])
        values = {"centre_distance_mm": actual, "expected_mm": expected, "axis_deg": tilt, "face_overlap_mm": overlap}
        problems = []
        if abs(actual - expected) > limit:
            problems.append(f"centre distance {actual:.3f} mm, needs {expected:.3f} ± {limit:g}")
        if tilt > tol.angle_deg:
            problems.append(f"axes {tilt:.2f}° apart")
        if overlap <= 0:
            problems.append("face widths do not overlap")
        detail = "; ".join(problems) or f"in mesh: centre distance {actual:.3f} mm (m{m:g}, {za:g}/{zb:g} teeth)"
        return RelationCheck(passed=not problems, detail=detail, values=values)

    def axes(self, fa: fr.Frame, fb: fr.Frame) -> tuple[Axis, Axis]:
        """Both turns are about a's Z: an external mesh turns them opposite ways."""
        return Axis("angle", fr.origin(fa), fr.z_axis(fa)), Axis("angle", fr.origin(fb), fr.z_axis(fa))

    def ratio(self, na: dict[str, float], nb: dict[str, float]) -> float:
        internal = bool(na.get("internal") or nb.get("internal"))
        return (1.0 if internal else -1.0) * na["teeth"] / nb["teeth"]


class RackRelation(_Relation):
    """A pinion (a, gear) on a rack (b, frame on the pitch line, Z across the rack, X along it): the pitch
    circle touches the pitch line. The rack moves π·m·z mm per pinion turn."""

    type: Literal["rack"]
    example = {"type": "rack", "a": "pinion.teeth", "b": "rack.pitch_line"}
    joins = ("gear", "frame")

    def verify(self, fa: fr.Frame, fb: fr.Frame, na: dict[str, float], nb: dict[str, float], tol: Any) -> RelationCheck:
        limit = self.tolerance or tol.position_mm
        pitch_r = na["module"] * na["teeth"] / 2
        actual = abs(fr.along(fr.origin(fa), fr.origin(fb), fr.z_axis(fb)))
        perpendicular = abs(90 - fr.angle_deg(fr.z_axis(fa), fr.x_axis(fb)))
        values = {"pitch_distance_mm": actual, "pitch_radius_mm": pitch_r, "axis_deg": perpendicular}
        problems = []
        if abs(actual - pitch_r) > limit:
            problems.append(f"pinion axis {actual:.3f} mm from the pitch line, needs {pitch_r:.3f}")
        if perpendicular > tol.angle_deg:
            problems.append(f"pinion axis {perpendicular:.2f}° off square to the rack")
        return RelationCheck(
            passed=not problems, detail="; ".join(problems) or "pinion on the pitch line", values=values
        )

    def axes(self, fa: fr.Frame, fb: fr.Frame) -> tuple[Axis, Axis]:
        """The rack travels along its X the way the pinion's pitch point moves."""
        za, zb, xb = fr.z_axis(fa), fr.z_axis(fb), fr.x_axis(fb)
        to_pitch_line = fr.along(fr.origin(fb), fr.origin(fa), zb) * zb
        sign = float(np.sign(np.dot(np.cross(za, to_pitch_line), xb)))
        return Axis("angle", fr.origin(fa), za), Axis("travel", fr.origin(fb), sign * xb)

    def ratio(self, na: dict[str, float], nb: dict[str, float]) -> float:
        return math.pi * na["module"] * na["teeth"] / 360.0


class ScrewRelation(_Relation):
    """A lead screw (a) and its nut (b) on one axis, right-hand thread: b travels `lead` mm per turn of a, back
    along a's Z as a turns positively about it."""

    type: Literal["screw"]
    lead: float = Field(gt=0, description="mm per turn")
    example = {"type": "screw", "a": "leadscrew.axis", "b": "nut.axis", "lead": 8}
    joins = ("frame", "frame")

    def verify(self, fa: fr.Frame, fb: fr.Frame, na: dict[str, float], nb: dict[str, float], tol: Any) -> RelationCheck:
        offset = fr.distance_to_line(fr.origin(fb), fr.origin(fa), fr.z_axis(fa))
        tilt = min(fr.angle_deg(fr.z_axis(fa), fr.z_axis(fb)), fr.angle_deg(fr.z_axis(fa), -fr.z_axis(fb)))
        values = {"axis_offset_mm": offset, "axis_deg": tilt}
        ok = offset <= (self.tolerance or tol.position_mm) and tilt <= tol.angle_deg
        return RelationCheck(passed=ok, detail="on one axis" if ok else f"axes {offset:.3f} mm, {tilt:.2f}° apart",
                             values=values)  # fmt: skip

    def axes(self, fa: fr.Frame, fb: fr.Frame) -> tuple[Axis, Axis]:
        return Axis("angle", fr.origin(fa), fr.z_axis(fa)), Axis("travel", fr.origin(fb), -fr.z_axis(fa))

    def ratio(self, na: dict[str, float], nb: dict[str, float]) -> float:
        return self.lead / 360.0


RelationSpec = Annotated[GearRelation | RackRelation | ScrewRelation, Field(discriminator="type")]
RELATION_TYPES: tuple[type[_Relation], ...] = members(RelationSpec)


# --------------------------------------------------------------------------- fastener sets


class FastenerSet(Model):
    """A catalog fastener through clearance-hole patterns (head side first) into a threaded or insert hole.
    `fastener` names the fastener's seat connector (under the head, Z along the shank); the fastener part's
    catalog provider must give its `thread_diameter` and `length`."""

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


def ref_names(ref: Ref) -> tuple[str, str]:
    """('interface', name) or ('parameter', name) for a reference."""
    return ("interface", ref.removeprefix("interfaces.")) if is_interface_ref(ref) else ("parameter", ref)
