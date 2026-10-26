"""The mate types: rigid, revolute, slider, cylindrical, planar and ball."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import Field

from hitlcad.core import frames as fr
from hitlcad.core.structure import Limits, Mate, MateCheck


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


class RigidMate(Mate):
    """Nothing moves: origins coincide (Z apart by `offset`), Z axes face each other, X axes agree up to
    the features' shared symmetry."""

    type: Literal["rigid"]
    offset: float = Field(default=0.0, description="Designed gap along a's Z, mm")
    example = {"type": "rigid", "a": "lid.bottom", "b": "housing.top"}
    holds = frozenset({"origin_xy", "origin_z", "axis", "clocking"})

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        target = fr.origin(fa) + fr.z_axis(fa) * self.offset
        values = {"origin_mm": float(np.linalg.norm(fr.origin(fb) - target)), "axis_deg": _opposed(fa, fb),
                  "clocking_deg": _clocking(fa, fb, symmetry)}  # fmt: skip
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values)


class RevoluteMate(Mate):
    """Turns about Z: origins coincide (Z apart by `offset`), Z axes face each other."""

    type: Literal["revolute"]
    offset: float = 0.0
    limits: Limits | None = None
    example = {"type": "revolute", "a": "carrier.pins", "b": "planet.bore", "fit": {"clearance": [0.2, 0.5]}}
    free_motion = ("angle",)
    holds = frozenset({"origin_xy", "origin_z", "axis"})

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        target = fr.origin(fa) + fr.z_axis(fa) * self.offset
        values = {"origin_mm": float(np.linalg.norm(fr.origin(fb) - target)), "axis_deg": _opposed(fa, fb)}
        angle = fr.signed_angle_deg(fr.x_axis(fa), fr.x_axis(fb), fr.z_axis(fa))
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values, free={"angle": angle})


class SliderMate(Mate):
    """Slides along Z: origins on one line along Z, Z axes face each other, X axes agree up to symmetry."""

    type: Literal["slider"]
    limits: Limits | None = None
    example = {"type": "slider", "a": "rail.track", "b": "carriage.runner", "limits": {"travel": [0, 120]}}
    free_motion = ("travel",)
    holds = frozenset({"origin_xy", "axis", "clocking"})

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        oa, za = fr.origin(fa), fr.z_axis(fa)
        values = {"axis_offset_mm": fr.distance_to_line(fr.origin(fb), oa, za), "axis_deg": _opposed(fa, fb),
                  "clocking_deg": _clocking(fa, fb, symmetry)}  # fmt: skip
        free = {"travel": fr.along(fr.origin(fb), oa, za)}
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values, free=free)


class CylindricalMate(Mate):
    """Turns about and slides along Z: origins on one line along Z, Z axes face each other."""

    type: Literal["cylindrical"]
    limits: Limits | None = None
    example = {"type": "cylindrical", "a": "motor.shaft", "b": "sun.bore", "fit": {"clearance": [0.0, 0.1]}}
    free_motion = ("angle", "travel")
    holds = frozenset({"origin_xy", "axis"})

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        oa, za = fr.origin(fa), fr.z_axis(fa)
        values = {"axis_offset_mm": fr.distance_to_line(fr.origin(fb), oa, za), "axis_deg": _opposed(fa, fb)}
        free = {"travel": fr.along(fr.origin(fb), oa, za),
                "angle": fr.signed_angle_deg(fr.x_axis(fa), fr.x_axis(fb), za)}  # fmt: skip
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values, free=free)


class PlanarMate(Mate):
    """Slides in the plane and turns about Z: b's origin on a's plane (apart by `offset`), Z axes face each other."""

    type: Literal["planar"]
    offset: float = 0.0
    example = {"type": "planar", "a": "table.top", "b": "box.bottom"}
    holds = frozenset({"origin_z", "axis"})

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        gap = fr.along(fr.origin(fb), fr.origin(fa), fr.z_axis(fa)) - self.offset
        values = {"plane_mm": gap, "axis_deg": _opposed(fa, fb)}
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values)


class BallMate(Mate):
    """Turns about its origin: the origins coincide."""

    type: Literal["ball"]
    example = {"type": "ball", "a": "arm.socket", "b": "rod.ball"}
    holds = frozenset({"origin_xy", "origin_z"})

    def verify(self, fa: fr.Frame, fb: fr.Frame, symmetry: int | Literal["axial"], tol: Any) -> MateCheck:
        values = {"origin_mm": float(np.linalg.norm(fr.origin(fb) - fr.origin(fa)))}
        return MateCheck(passed=(s := _summary(values, tol))[0], detail=s[1], values=values)
