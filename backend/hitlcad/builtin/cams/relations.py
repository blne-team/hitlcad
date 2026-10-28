"""The cam relation: a cam turning about its axis lifts a follower along its own, by a table the design's code
computes (the code that cuts the profile), and the two stay in contact."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from hitlcad.core import frames as fr
from hitlcad.core.laws import Table
from hitlcad.core.structure import Axis, Ends, Ref, Relation, RelationCheck


class CamRelation(Relation):
    """A cam (a: Z along its axis, X where its law's angle is 0) lifting a follower (b: at the point its lift is
    given for, a flat face's or a roller's centre, Z along its travel, away from the cam): with the cam turned θ
    about its Z from where its X points along the follower's Z, the follower sits `base` + law(θ) from the cam's
    axis, along its Z. Its law is `lift`, an interface value: [[θ°, lift mm], …] over one turn, ascending in θ. Any part
    turning against a follower is a cam: a rocker arm pressing a valve stem. Verified: the follower's travel
    square to the cam's axis, and its place as assembled the law's, within `tolerance`. In motion the follower
    follows the cam (it cannot drive it back), and the two stay in contact."""

    type: Literal["cam"]
    lift: Ref = Field(description="The interface value of its lift table, e.g. 'interfaces.intake_lift'")
    base: float | Ref = Field(description="mm from the cam's axis to the follower's frame at no lift (its base "
                              "circle, plus a roller's radius), or the parameter or interface value that gives it")  # fmt: skip
    example = {"type": "cam", "a": "camshaft.lobe", "b": "lifter.face", "lift": "interfaces.intake_lift",
               "base": "base_radius"}  # fmt: skip
    joins = ("frame", "frame")
    touching = True

    def verify(self, ends: Ends, tol: Any) -> RelationCheck:
        base = float(ends.value(self.base)) if isinstance(self.base, str) else self.base
        table, lift = self.law(ends), _reach(ends.fa, ends.fb) - base
        law = table.of(table.start)
        square = abs(90.0 - fr.angle_deg(fr.z_axis(ends.fa), fr.z_axis(ends.fb)))
        values = {"angle_deg": table.start, "lift_mm": lift, "law_mm": law, "square_deg": square}
        problems = []
        if square > tol.angle_deg:
            problems.append(f"the follower's travel is {square:.2f}° off square to the cam's axis")
        if abs(lift - law) > (self.tolerance or tol.position_mm):
            problems.append(f"the follower sits at {lift:.3f} mm lift with the cam at {table.start:.1f}°, where its "
                            f"law lifts it {law:.3f} mm")  # fmt: skip
        detail = "; ".join(problems) or f"the follower at {lift:.3f} mm lift, its law's at {table.start:.1f}°"
        return RelationCheck(passed=not problems, detail=detail, values=values)

    def axes(self, fa: fr.Frame, fb: fr.Frame) -> tuple[Axis, Axis]:
        return Axis("angle", fr.origin(fa), fr.z_axis(fa)), Axis("travel", fr.origin(fb), fr.z_axis(fb))

    def law(self, ends: Ends) -> Table:
        """The lift table, from the cam's angle as assembled."""
        points = tuple((float(x), float(y)) for x, y in ends.value(self.lift))
        return Table(points, start=_angle(ends.fa, ends.fb), period=360.0)

    def coordinates(self, fa0: fr.Frame, fb0: fr.Frame, fa: Any, fb: Any) -> tuple[Any, Any]:
        """The cam's turn against the follower's line, and the follower's travel along it from the cam's axis."""
        return fr.wrapped(_angle(fa, fb) - _angle(fa0, fb0)), _reach(fa, fb) - _reach(fa0, fb0)


def _angle(fa: Any, fb: Any) -> Any:
    """How far the cam has turned about its Z from where its X points along the follower's Z (degrees)."""
    return -fr.heading(fa, fb[:3, 2])


def _reach(fa: Any, fb: Any) -> Any:
    """How far the follower's frame is from the cam's axis, along the follower's Z (mm)."""
    return -fr.coordinate(fa[:3, 3], fb, 2)
