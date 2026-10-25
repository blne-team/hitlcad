"""Requirement checks and manufacturing processes: each a model that says what it means (docstring,
example) and verifies or checks itself on what the build measured.

Checks target an address: a part (measured in its own frame, the way it is made), an instance or
component path (measured where it is placed), or the whole design when no target is given.
"""

from __future__ import annotations

from abc import abstractmethod
from collections import Counter
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal

from pydantic import Field, model_validator

from hitlcad.core.types import Model, members

if TYPE_CHECKING:
    from hitlcad.analysis.model import Body, BuildBodies

Verdict = tuple[bool | None, str]  # (passed, detail); None: this build cannot tell, and the detail says why
#: A requirement's number: as written, or a parameter's name or "interfaces.<name>" that gives it, so a
#: requirement follows the design ("as tall as the height parameter") in every configuration.
Number = float | str

AXES = {"x": 0, "y": 1, "z": 2}


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
    def verify(self, build: BuildBodies) -> Verdict: ...

    def targets(self) -> list[str]:
        return []


class _Bounded(Model):
    min: Number | None = None
    max: Number | None = None

    @model_validator(mode="after")
    def _bounded(self) -> _Bounded:
        if self.min is None and self.max is None:
            raise ValueError("give min, max or both")
        if isinstance(self.min, float) and isinstance(self.max, float) and self.min > self.max:
            raise ValueError("min is greater than max")
        return self

    def bounds(self, value: float, build: BuildBodies, unit: str) -> Verdict:
        return _bounds(value, build.number(self.min), build.number(self.max), unit)


class _OnTarget(_Check):
    target: str | None = Field(
        default=None,
        description="A part (its own frame), an instance or component path (placed); default: the whole design",
    )

    def subject(self, build: BuildBodies) -> Body:
        return build.subject(self.target)

    def targets(self) -> list[str]:
        return [self.target] if self.target else []


class SizeCheck(_Bounded, _OnTarget):
    """The extent along one axis, mm."""

    kind: Literal["size"]
    axis: Literal["x", "y", "z"]
    example = {"kind": "size", "axis": "z", "min": 9.9, "max": "interfaces.height_max"}

    def verify(self, build: BuildBodies) -> Verdict:
        passed, detail = self.bounds(self.subject(build).metrics.size[AXES[self.axis]], build, "mm")
        return passed, f"{self.axis.upper()} {detail}"


class FitsWithinCheck(_OnTarget):
    """Fits inside a box of this size (X, Y, Z mm), as it is modelled."""

    kind: Literal["fits_within"]
    box: tuple[Number, Number, Number]
    example = {"kind": "fits_within", "box": [120, 80, 40]}

    def verify(self, build: BuildBodies) -> Verdict:
        size = self.subject(build).metrics.size
        limit = [build.number(b) for b in self.box]
        over = [f"{axis.upper()} {size[i]:.2f} > {limit[i]:g}" for axis, i in AXES.items() if size[i] > limit[i]]
        box = " × ".join(f"{b:g}" for b in limit)
        return not over, f"{' × '.join(f'{v:.2f}' for v in size)} mm in a {box} mm box" + (
            f": {', '.join(over)}" if over else ""
        )


class VolumeCheck(_Bounded, _OnTarget):
    """Material volume, mm³."""

    kind: Literal["volume"]
    example = {"kind": "volume", "max": 50000}

    def verify(self, build: BuildBodies) -> Verdict:
        volume = self.subject(build).metrics.volume
        if volume is None:
            return None, "volume unknown: the mesh is not closed"
        return self.bounds(volume, build, "mm³")


class MassCheck(_Bounded, _OnTarget):
    """Mass in grams, from each part's volume and the density of its material."""

    kind: Literal["mass"]
    example = {"kind": "mass", "max": 60}

    def verify(self, build: BuildBodies) -> Verdict:
        mass, unknown = self.subject(build).mass_g()
        if unknown:
            return None, f"mass unknown: {unknown}"
        assert mass is not None
        return self.bounds(mass, build, "g")


class HoleCheck(_OnTarget):
    """At least `count` complete round holes of this diameter (through or blind), found on the B-rep."""

    kind: Literal["hole"]
    diameter: Number = Field(description="mm")
    tol: Number = Field(default=0.1, description="mm either way")
    count: int = Field(default=1, ge=1)
    example = {"kind": "hole", "diameter": 6, "tol": 0.1, "count": 2}

    def verify(self, build: BuildBodies) -> Verdict:
        from hitlcad.analysis.features import Unmeasurable

        body = self.subject(build)
        try:
            holes = body.holes()
        except Unmeasurable as e:
            return None, f"{body.name}: {e}"
        diameter, tol = build.number(self.diameter), build.number(self.tol)
        matching = [h for h in holes if abs(h.diameter - diameter) <= tol]
        found = Counter(f"Ø{h.diameter:.2f}" for h in holes)
        inventory = ", ".join(f"{d} ×{n}" for d, n in sorted(found.items())) or "none"
        return len(matching) >= self.count, (
            f"{len(matching)} of Ø{diameter:g} ±{tol:g} mm (needs {self.count}); holes: {inventory}"
        )


class WallCheck(_OnTarget):
    """No wall thinner than `min` mm."""

    kind: Literal["wall"]
    min: Number
    example = {"kind": "wall", "min": 2}

    def verify(self, build: BuildBodies) -> Verdict:
        thinnest = self.subject(build).walls.thinnest
        if thinnest is None:
            return None, "wall thickness not measurable: the mesh is not closed"
        minimum = build.number(self.min)
        return thinnest >= minimum, f"thinnest wall {thinnest:.2f} mm (needs ≥ {minimum:g})"


class BodiesCheck(_OnTarget):
    """The number of separate pieces."""

    kind: Literal["bodies"]
    count: int = Field(ge=1)
    example = {"kind": "bodies", "count": 1}

    def verify(self, build: BuildBodies) -> Verdict:
        bodies = self.subject(build).metrics.bodies
        return bodies == self.count, f"{bodies} (needs {self.count})"


class ClearanceCheck(_Bounded, _Check):
    """The gap between two placed things (instances or component instances), mm. Things that overlap
    have no gap and fail any minimum. Clearance is analysis, not a mate: use it for what must never touch."""

    kind: Literal["clearance"]
    between: tuple[str, str]
    example = {"kind": "clearance", "between": ["carrier", "housing"], "min": 0.5}

    def targets(self) -> list[str]:
        return list(self.between)

    def verify(self, build: BuildBodies) -> Verdict:
        from hitlcad.analysis.features import Unmeasurable

        a, b = (build.placed(name) for name in self.between)
        try:
            shared = a.overlap(b)
            if shared > build.overlap_mm3:
                return self.min is None, f"{a.name} and {b.name} overlap by {shared:.1f} mm³"
            passed, detail = self.bounds(a.gap(b), build, "mm")
        except Unmeasurable as e:  # a mesh is not closed
            return None, f"not measurable: {e}"
        return passed, f"{a.name} to {b.name}: {detail}"


class StackCheck(_Bounded, _Check):
    """A tolerance stack: the gap between two connector frames along the first one's Z, as built (nominal),
    worst case (± the sum of the contributors' tolerances) and statistically (± their root sum of squares).
    `tolerances` maps each contributing part to its ± tolerance in mm, as the process holds it; the check
    needs both the worst case and the nominal within min and max, and reports the RSS beside them."""

    kind: Literal["stack"]
    between: tuple[str, str] = Field(description="Connector ends, e.g. ['housing.top', 'carrier.output']")
    tolerances: dict[str, float] = Field(min_length=1, description="Contributing part -> ± tolerance, mm")
    example = {
        "kind": "stack",
        "between": ["carrier.axis", "housing.top"],
        "tolerances": {"carrier": 0.1, "housing": 0.15},
        "min": 0.2,
    }

    def targets(self) -> list[str]:
        return [*self.between, *self.tolerances]

    def verify(self, build: BuildBodies) -> Verdict:
        import math

        from hitlcad.core import frames as fr

        a, b = (build.built.frames(end) for end in self.between)
        if len(a) != 1 or len(b) != 1:
            return (
                False,
                f"a stack runs between two single frames; {self.between[0]} has {len(a)}, {self.between[1]} {len(b)}",
            )
        unknown = sorted(set(self.tolerances) - set(build.built.parts))
        if unknown:
            return False, f"tolerances name {', '.join(unknown)}, which are not parts of this design"
        nominal = fr.along(fr.origin(b[0].frame), fr.origin(a[0].frame), fr.z_axis(a[0].frame))
        worst = sum(abs(t) for t in self.tolerances.values())
        rss = math.sqrt(sum(t * t for t in self.tolerances.values()))
        low, high = nominal - worst, nominal + worst
        least, most = build.number(self.min), build.number(self.max)
        ok = (least is None or low >= least) and (most is None or high <= most)
        need = " and ".join(p for p in (f"≥ {least:g}" if least is not None else "",
                                         f"≤ {most:g}" if most is not None else "") if p)  # fmt: skip
        return ok, (f"{nominal:.3f} mm nominal, {low:.3f} … {high:.3f} worst case (± {worst:.3f}), ± {rss:.3f} RSS "
                    f"over {len(self.tolerances)} parts (needs {need})")  # fmt: skip


RequirementCheck = Annotated[
    SizeCheck
    | FitsWithinCheck
    | VolumeCheck
    | MassCheck
    | HoleCheck
    | WallCheck
    | BodiesCheck
    | ClearanceCheck
    | StackCheck,
    Field(discriminator="kind"),
]
REQUIREMENT_CHECKS: tuple[type[_Check], ...] = members(RequirementCheck)


class Requirement(Model):
    """Something the design must satisfy. With a `check`, every build verifies it; without one, the human judges."""

    text: str
    check: RequirementCheck | None = None


# --------------------------------------------------------------------------- manufacturing


class ProcessFinding(Model):
    name: str
    passed: bool | None
    detail: str


class _Process(Model):
    """How a part is made. Every build checks each made part for its process, in the part's own frame
    (the way it is made)."""

    example: ClassVar[dict[str, Any]]

    @abstractmethod
    def checks(self, body: Body) -> list[ProcessFinding]: ...

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        """How much the process makes a round feature's diameter smaller (hole) or larger (shaft) than modelled, mm."""
        return 0.0


class FdmProcess(_Process):
    """FDM (filament) 3D printing, printed the way the part stands in its own frame (+Z up, on its lowest
    Z): wall thickness, overhangs and bed contact. An overhang prints where it bridges between two supports
    no more than `max_bridge_mm` apart (the top of a horizontal hole is such a bridge) or lies within a
    nozzle's width of what holds it. With `supports`, overhangs print on support and are reported, not
    failed: for shapes that cannot avoid them, like figurines. With `brim`, a tall, narrow part is held down
    by a brim. `hole_allowance_mm` / `shaft_allowance_mm`: how much printed holes come out smaller and shafts
    larger than modelled, for fit checks."""

    process: Literal["fdm"]
    nozzle_mm: float = Field(default=0.4, gt=0)
    min_wall_mm: float = Field(default=0.8, gt=0, description="Thinnest wall that prints solid: two perimeters")
    max_overhang_deg: float = Field(
        default=45, ge=0, lt=90, description="Steepest overhang, from vertical, without support"
    )
    max_bridge_mm: float = Field(default=10.0, ge=0, description="Longest span bridged between two supports")
    min_bed_contact_share: float = Field(
        default=0.10, ge=0, le=1, description="Of the part's outline seen from above, the share its base must span"
    )
    max_height_to_base: float = Field(
        default=4.0, gt=0, description="Tallest a part stands, × its base's narrowest width, without a brim"
    )
    ignored_overhang_mm2: float = Field(
        default=1.0, ge=0, description="An overhang that prints on nothing over less than this still prints"
    )
    supports: bool = Field(default=False, description="Printed with support under its overhangs")
    brim: bool = Field(default=False, description="Printed with a brim round its base")
    hole_allowance_mm: float = Field(default=0.0, ge=0)
    shaft_allowance_mm: float = Field(default=0.0, ge=0)
    example = {"process": "fdm", "hole_allowance_mm": 0.15}

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.analysis.processes import fdm_checks

        return fdm_checks(body, self)

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        return self.hole_allowance_mm if feature == "hole" else self.shaft_allowance_mm


class ResinProcess(_Process):
    """Resin 3D printing (SLA, DLP, MSLA), on support: walls of at least `min_wall_mm`, and every hollow
    inside the part drained through an opening at least `min_drain_mm` across, or it traps uncured resin
    (Formlabs' design specifications ask for 3.5 mm). `hole_allowance_mm` / `shaft_allowance_mm` as for FDM."""

    process: Literal["resin"]
    min_wall_mm: float = Field(default=0.6, gt=0, description="Thinnest wall that prints and survives washing")
    min_drain_mm: float = Field(default=3.5, gt=0, description="Narrowest opening resin drains out of a hollow by")
    hole_allowance_mm: float = Field(default=0.0, ge=0)
    shaft_allowance_mm: float = Field(default=0.0, ge=0)
    example = {"process": "resin", "min_wall_mm": 0.6}

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.analysis.processes import resin_checks

        return resin_checks(body, self)

    def allowance(self, feature: Literal["hole", "shaft"]) -> float:
        return self.hole_allowance_mm if feature == "hole" else self.shaft_allowance_mm


class CncMillingProcess(_Process):
    """3-axis CNC milling from +Z (the part's own frame): every face but the bottom reachable along a clear
    path up from it, concave vertical corners with at least the tool radius, and no corner deeper than
    `max_depth_ratio` × the cutter that finishes it (its own diameter, or the tool's where it is sharp)."""

    process: Literal["cnc_milling"]
    tool_diameter_mm: float = Field(default=6.0, gt=0)
    max_depth_ratio: float = Field(default=4.0, gt=0)
    ignored_area_mm2: float = Field(
        default=1.0, ge=0, description="Faces out of reach over less than this (a sliver at a seam) do not fail"
    )
    example = {"process": "cnc_milling", "tool_diameter_mm": 6}

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.analysis.processes import cnc_milling_checks

        return cnc_milling_checks(body, self)


class SheetMetalProcess(_Process):
    """Sheet metal: the thinnest and the median wall are `thickness_mm` (± `thickness_tolerance_mm`), and
    every bend's inner radius is at least `min_bend_radius_mm`. A bend is a pair of coaxial cylinders, inside
    r and outside r + the thickness, or a sharp inside edge between two faces each with the sheet's other
    side behind it (radius 0); the corners of a cut-out are not bends."""

    process: Literal["sheet_metal"]
    thickness_mm: float = Field(gt=0)
    thickness_tolerance_mm: float = Field(default=0.05, ge=0)
    min_bend_radius_mm: float = Field(gt=0)
    example = {"process": "sheet_metal", "thickness_mm": 1.5, "min_bend_radius_mm": 1.5}

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.analysis.processes import sheet_metal_checks

        return sheet_metal_checks(body, self)


class InjectionMoldingProcess(_Process):
    """Injection molding in a two-plate mold that opens along Z of the part's own frame: faces along the pull
    need at least `min_draft_deg` of draft, every face must leave its mold half on a clear path along Z (no
    undercuts), and no wall may be over `max_wall_ratio` × the median wall."""

    process: Literal["injection_molding"]
    min_draft_deg: float = Field(default=1.0, ge=0)
    max_wall_ratio: float = Field(default=2.0, gt=1, description="Thickest wall, × the median wall")
    ignored_area_mm2: float = Field(
        default=1.0, ge=0, description="Faces that fail over less than this (a sliver at a seam) do not fail"
    )
    example = {"process": "injection_molding", "min_draft_deg": 1}

    def checks(self, body: Body) -> list[ProcessFinding]:
        from hitlcad.analysis.processes import injection_molding_checks

        return injection_molding_checks(body, self)


Manufacturing = Annotated[
    FdmProcess | ResinProcess | CncMillingProcess | SheetMetalProcess | InjectionMoldingProcess,
    Field(discriminator="process"),
]
MANUFACTURING_PROCESSES: tuple[type[_Process], ...] = members(Manufacturing)
