"""The requirement checks: size, fits within, volume, mass, holes, walls, bodies, clearance, tolerance stacks and
spans."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from hitlcad.core.checks import AXES, Bounded, Check, Number, OnTarget, Verdict, need
from hitlcad.kernel.errors import Unmeasurable

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuildBodies


class SizeCheck(Bounded, OnTarget):
    """The extent along one axis, mm."""

    kind: Literal["size"]
    axis: Literal["x", "y", "z"]
    example = {"kind": "size", "axis": "z", "min": 9.9, "max": "interfaces.height_max"}
    hint = "Change what sets its extent along the axis, or the bounds where they are wrong."

    def verify(self, build: BuildBodies) -> Verdict:
        passed, detail = self.bounds(self.subject(build).metrics.size[AXES[self.axis]], build, "mm")
        return passed, f"{self.axis.upper()} {detail}"


class FitsWithinCheck(OnTarget):
    """Fits inside a box of this size (X, Y, Z mm), as it is modelled."""

    kind: Literal["fits_within"]
    box: tuple[Number, Number, Number]
    example = {"kind": "fits_within", "box": [120, 80, 40]}
    hint = "Shrink or turn what sticks out of the box (the message gives each axis)."

    def verify(self, build: BuildBodies) -> Verdict:
        size = self.subject(build).metrics.size
        limit = [build.number(b) for b in self.box]
        over = [f"{axis.upper()} {size[i]:.2f} > {limit[i]:g}" for axis, i in AXES.items() if size[i] > limit[i]]
        box = " × ".join(f"{b:g}" for b in limit)
        return not over, f"{' × '.join(f'{v:.2f}' for v in size)} mm in a {box} mm box" + (
            f": {', '.join(over)}" if over else ""
        )


class VolumeCheck(Bounded, OnTarget):
    """Material volume, mm³."""

    kind: Literal["volume"]
    example = {"kind": "volume", "max": 50000}
    hint = "Add or remove material where it matters least: walls, pockets, ribs."

    def verify(self, build: BuildBodies) -> Verdict:
        volume = self.subject(build).metrics.volume
        if volume is None:
            return None, "volume unknown: the mesh is not closed"
        return self.bounds(volume, build, "mm³")


class MassCheck(Bounded, OnTarget):
    """Mass in grams, from each part's volume and the density of its material."""

    kind: Literal["mass"]
    example = {"kind": "mass", "max": 60}
    hint = "Change the volume of the heaviest parts, or their material."

    def verify(self, build: BuildBodies) -> Verdict:
        mass, unknown = self.subject(build).mass_g()
        if mass is None:
            return None, f"mass unknown: {unknown}"
        return self.bounds(mass, build, "g")


class HoleCheck(OnTarget):
    """At least `count` complete round holes of this diameter (through or blind), found on the B-rep."""

    kind: Literal["hole"]
    diameter: Number = Field(description="mm")
    tol: Number = Field(default=0.1, description="mm either way")
    count: int = Field(default=1, ge=1)
    example = {"kind": "hole", "diameter": 6, "tol": 0.1, "count": 2}
    hint = "Model the holes as complete round holes of the diameter asked, on a B-rep backend."

    def verify(self, build: BuildBodies) -> Verdict:

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


class WallCheck(OnTarget):
    """No wall thinner than `min` mm."""

    kind: Literal["wall"]
    min: Number
    example = {"kind": "wall", "min": 2}
    hint = "Thicken the walls where the message places them."

    def verify(self, build: BuildBodies) -> Verdict:

        try:
            thinnest = self.subject(build).walls.thinnest
        except Unmeasurable as e:
            return None, f"wall thickness not measurable: {e}"
        minimum = build.number(self.min)
        return thinnest >= minimum, f"thinnest wall {thinnest:.2f} mm (needs ≥ {minimum:g})"


class BodiesCheck(OnTarget):
    """The number of separate pieces."""

    kind: Literal["bodies"]
    count: int = Field(ge=1)
    example = {"kind": "bodies", "count": 1}
    hint = "Join pieces meant to be one (they must share volume, not only touch), or part those meant apart."

    def verify(self, build: BuildBodies) -> Verdict:
        bodies = self.subject(build).metrics.bodies
        return bodies == self.count, f"{bodies} (needs {self.count})"


class ClearanceCheck(Bounded, Check):
    """The gap between two placed things (instances or component instances), mm: the least it comes to where
    they sit and wherever a mate's range of motion moves one against the other. Things that overlap have no
    gap and fail any minimum. Clearance is analysis, not a mate: use it for what must never touch."""

    kind: Literal["clearance"]
    between: tuple[str, str]
    example = {"kind": "clearance", "between": ["carrier", "housing"], "min": 0.5}
    hint = (
        "Move them apart where they come closest (the message says where), or limit the motion that brings "
        "them together."
    )

    def targets(self) -> list[str]:
        return list(self.between)

    def keeps_apart(self) -> tuple[tuple[str, str], Number] | None:
        return self.between, self.min or 0.0

    def verify(self, build: BuildBodies) -> Verdict:

        a, b = (build.placed(name) for name in self.between)
        try:
            shared = a.overlap(b)
            if shared > build.overlap_mm3:
                return self.min is None, f"{a.name} and {b.name} overlap by {shared:.1f} mm³"
            sitting = a.gap(b)
            moved = build.moved_gap(a, b)
        except Unmeasurable as e:  # a mesh is not closed
            return None, f"not measurable: {e}"
        least, where, complete = (moved.gap_mm, moved.where, moved.complete) if moved else (sitting, "", True)
        if least >= sitting:
            passed, detail = self.bounds(sitting, build, "mm")
            detail = f"{a.name} to {b.name}: {detail}"
        else:
            passed, detail = self.bounds(least, build, "mm")
            detail = f"{a.name} to {b.name}: {detail} {where}; {sitting:.2f} mm where they sit"
        if passed and not complete:
            return None, f"{detail}; its motion was not swept to the ends (see motion)"
        return passed, detail

    def cause(self, build: BuildBodies) -> str:
        """The two overlap where they sit, or a mate's motion brings one against the other."""
        from hitlcad.core.report import Cause

        a, b = (build.placed(name) for name in self.between)
        if a.overlap(b) > build.overlap_mm3:
            return Cause.overlap(*self.between)
        moved = build.moved_gap(a, b)
        return Cause.contact(moved.mate, *self.between) if moved and moved.gap_mm <= 0 else ""


class StackCheck(Bounded, Check):
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
    hint = "Change the nominal gap, or the contributors' tolerances."

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
        return ok, (f"{nominal:.3f} mm nominal, {low:.3f} … {high:.3f} worst case (± {worst:.3f}), ± {rss:.3f} RSS "
                    f"over {len(self.tolerances)} parts (needs {need(least, most)})")  # fmt: skip


class SpanCheck(Bounded, Check):
    """The length between two connector frames' origins, mm: where they sit and at every position motion moves
    either through (a spring's ends, from its solid height to its free length less the preload it must keep)."""

    kind: Literal["span"]
    between: tuple[str, str] = Field(description="Connector ends, e.g. ['lifter.spring_seat', 'head.spring_seat']")
    example = {"kind": "span", "between": ["follower.spring", "base.spring"], "min": 18, "max": "free_length"}
    hint = "Change where the ends sit, or the motion that moves them, or the bounds (a spring's lengths)."

    def targets(self) -> list[str]:
        return list(self.between)

    def verify(self, build: BuildBodies) -> Verdict:
        a, b = (build.built.frames(end) for end in self.between)
        if len(a) != 1 or len(b) != 1:
            return (
                False,
                f"a span runs between two single frames; {self.between[0]} has {len(a)}, {self.between[1]} {len(b)}",
            )
        sitting, moved = build.lengths(a[0], b[0])
        least, most = build.number(self.min), build.number(self.max)
        lo, hi = min([(sitting, ""), *moved]), max([(sitting, ""), *moved])
        ok = (least is None or lo[0] >= least) and (most is None or hi[0] <= most)
        through = f", {lo[0]:.2f} … {hi[0]:.2f} mm through {', '.join(sorted({m for _, m in moved}))}" if moved else ""
        return ok, f"{sitting:.2f} mm where they sit{through} (needs {need(least, most)})"
