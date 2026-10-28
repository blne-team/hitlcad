"""The relations: gears in mesh, a pinion on a rack, a lead screw and its nut."""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from pydantic import Field

from hitlcad.core import frames as fr
from hitlcad.core.laws import Linear
from hitlcad.core.structure import Axis, Ends, Relation, RelationCheck


class GearRelation(Relation):
    """Two spur gears in mesh: same module, parallel axes, centre distance m(z₁ ± z₂)/2 (minus when one is
    internal), overlapping face widths. b turns −z_a/z_b times a (+ for an internal gear)."""

    type: Literal["gear"]
    example = {"type": "gear", "a": "sun.teeth", "b": "planet.teeth"}
    joins = ("gear", "gear")

    def verify(self, ends: Ends, tol: Any) -> RelationCheck:
        fa, fb, na, nb = ends.fa, ends.fb, ends.na, ends.nb
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

    def law(self, ends: Ends) -> Linear:
        internal = bool(ends.na.get("internal") or ends.nb.get("internal"))
        return Linear((1.0 if internal else -1.0) * ends.na["teeth"] / ends.nb["teeth"])

    def coordinates(self, fa0: fr.Frame, fb0: fr.Frame, fa: Any, fb: Any) -> tuple[Any, Any]:
        """Each gear's turn against the line of their centres, about a's Z: on a moving carrier, Willis's."""
        line0, line = fb0[:3, 3] - fa0[:3, 3], fb[:3, 3] - fa[:3, 3]
        sign = 1.0 if np.dot(fr.z_axis(fa0), fr.z_axis(fb0)) >= 0 else -1.0
        return _turned(fa0, fa, line0, line), sign * _turned(fb0, fb, line0, line)


class RackRelation(Relation):
    """A pinion (a, gear) on a rack (b, frame on the pitch line, Z across the rack, X along it): the pitch
    circle touches the pitch line. The rack moves π·m·z mm per pinion turn."""

    type: Literal["rack"]
    example = {"type": "rack", "a": "pinion.teeth", "b": "rack.pitch_line"}
    joins = ("gear", "frame")

    def verify(self, ends: Ends, tol: Any) -> RelationCheck:
        fa, fb, na = ends.fa, ends.fb, ends.na
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

    def law(self, ends: Ends) -> Linear:
        return Linear(math.pi * ends.na["module"] * ends.na["teeth"] / 360.0)

    def coordinates(self, fa0: fr.Frame, fb0: fr.Frame, fa: Any, fb: Any) -> tuple[Any, Any]:
        """The pinion's turn against the rack, and the rack's travel past the pinion's axis, the way `axes` has it."""
        sign = float(np.dot(self.axes(fa0, fb0)[1].direction, fr.x_axis(fb0)))
        travel = fr.coordinate(fa[:3, 3], fb, 0) - fr.coordinate(fa0[:3, 3], fb0, 0)
        return _turned(fa0, fa, fb0[:3, 0], fb[:3, 0]), -sign * travel


class ScrewRelation(Relation):
    """A lead screw (a) and its nut (b) on one axis, right-hand thread: b travels `lead` mm per turn of a, back
    along a's Z as a turns positively about it."""

    type: Literal["screw"]
    lead: float = Field(gt=0, description="mm per turn")
    example = {"type": "screw", "a": "leadscrew.axis", "b": "nut.axis", "lead": 8}
    joins = ("frame", "frame")

    def verify(self, ends: Ends, tol: Any) -> RelationCheck:
        fa, fb = ends.fa, ends.fb
        offset = fr.distance_to_line(fr.origin(fb), fr.origin(fa), fr.z_axis(fa))
        tilt = min(fr.angle_deg(fr.z_axis(fa), fr.z_axis(fb)), fr.angle_deg(fr.z_axis(fa), -fr.z_axis(fb)))
        values = {"axis_offset_mm": offset, "axis_deg": tilt}
        ok = offset <= (self.tolerance or tol.position_mm) and tilt <= tol.angle_deg
        return RelationCheck(passed=ok, detail="on one axis" if ok else f"axes {offset:.3f} mm, {tilt:.2f}° apart",
                             values=values)  # fmt: skip

    def axes(self, fa: fr.Frame, fb: fr.Frame) -> tuple[Axis, Axis]:
        return Axis("angle", fr.origin(fa), fr.z_axis(fa)), Axis("travel", fr.origin(fb), -fr.z_axis(fa))

    def law(self, ends: Ends) -> Linear:
        return Linear(self.lead / 360.0)

    def coordinates(self, fa0: fr.Frame, fb0: fr.Frame, fa: Any, fb: Any) -> tuple[Any, Any]:
        """The screw's turn against the nut, and the nut's travel back along the screw's Z."""
        return _turned(fa0, fa, fb0[:3, 0], fb[:3, 0]), -(
            fr.coordinate(fb[:3, 3], fa, 2) - fr.coordinate(fb0[:3, 3], fa0, 2)
        )


def _turned(f0: fr.Frame, f: Any, d0: np.ndarray, d: Any) -> Any:
    """How far a frame has turned about its own Z (degrees, in (-180, 180]) against a direction that went from d0
    to d: the direction, seen from the frame, turns the other way."""
    return -fr.wrapped(fr.heading(f, d) - fr.heading(f0, d0))
