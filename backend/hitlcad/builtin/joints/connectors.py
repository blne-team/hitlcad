"""The connector kinds of joints: a datum frame, a planar face, a round hole and a round shaft."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from hitlcad.core import frames as fr
from hitlcad.core.structure import Connector, Measured, Tolerances

if TYPE_CHECKING:
    from hitlcad.geometry.features import Features


def vec(v: Any) -> np.ndarray:
    """A build123d vector as an array."""
    return np.array([v.X, v.Y, v.Z], dtype=float)


def _on_axis(item: Any, frame: fr.Frame, tol: Tolerances) -> bool:
    """Whether a face is a cylinder about the frame's Z axis."""
    if item.geom_type.name != "CYLINDER":
        return False
    axis = item.axis_of_rotation
    d = vec(axis.direction)
    o, z = fr.origin(frame), fr.z_axis(frame)
    return min(fr.angle_deg(d, z), fr.angle_deg(d, -z)) <= tol.angle_deg and \
        fr.distance_to_line(o, vec(axis.position), d) <= tol.position_mm  # fmt: skip


class FrameConnector(Connector):
    """A datum: a position and orientation with no feature to measure (an axis, a reference point)."""

    kind: Literal["frame"]
    example = {"kind": "frame", "symmetry": "axial"}
    measures = False

    def measure(self, features: Features, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        return Measured(status="pass", detail="datum")


class FaceConnector(Connector):
    """A planar face. Origin on its plane, inside its outline (the centre of a rim is fine), Z its outward
    normal. Measured: the plane and its area."""

    kind: Literal["face"]
    example = {"kind": "face", "symmetry": 4}

    def measure(self, features: Features, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        return features.face(frame, tol)

    def owns(self, item: Any, frame: fr.Frame, tol: Tolerances) -> bool:
        o, z = fr.origin(frame), fr.z_axis(frame)
        return (item.geom_type.name == "PLANE" and fr.angle_deg(vec(item.normal_at()), z) <= tol.angle_deg
                and abs(fr.along(vec(item.center()), o, z)) <= tol.position_mm)  # fmt: skip


class HoleConnector(Connector):
    """A round hole. Origin at its opening on the axis, Z out of the opening. Measured: the diameter and
    depth of the hole adjacent to the origin, below it."""

    kind: Literal["hole"]
    example = {"kind": "hole", "count": 4}
    round = "hole"

    def measure(self, features: Features, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        return features.round(frame, tol, inward=True)

    def owns(self, item: Any, frame: fr.Frame, tol: Tolerances) -> bool:
        return _on_axis(item, frame, tol)


class ShaftConnector(Connector):
    """A round shaft or pin. Origin at its shoulder on the axis, Z towards its tip. Measured: the
    diameter and length of the shaft adjacent to the origin, along Z."""

    kind: Literal["shaft"]
    example = {"kind": "shaft"}
    round = "shaft"

    def measure(self, features: Features, frame: fr.Frame, tol: Tolerances, nominals: dict[str, float]) -> Measured:
        return features.round(frame, tol, inward=False)

    def owns(self, item: Any, frame: fr.Frame, tol: Tolerances) -> bool:
        return _on_axis(item, frame, tol)
