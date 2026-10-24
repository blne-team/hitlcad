"""Coordinate frames as 4 × 4 homogeneous transforms (mm), and the measurements mates make between them."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt

from hitlcad.core.types import Matrix

Frame = npt.NDArray[np.float64]  # 4 × 4

IDENTITY: Frame = np.eye(4)


def as_frame(m: Matrix | list[list[float]] | Frame) -> Frame:
    frame = np.asarray(m, dtype=float)
    if frame.shape != (4, 4):
        raise ValueError(f"a frame is a 4 × 4 matrix, got shape {frame.shape}")
    return frame


def as_matrix(frame: Frame) -> Matrix:
    return tuple(tuple(round(float(v), 9) + 0.0 for v in row) for row in frame)  # type: ignore[return-value]


def origin(f: Frame) -> npt.NDArray[np.float64]:
    return f[:3, 3]


def x_axis(f: Frame) -> npt.NDArray[np.float64]:
    return _unit(f[:3, 0])


def z_axis(f: Frame) -> npt.NDArray[np.float64]:
    return _unit(f[:3, 2])


def _unit(v: Any) -> npt.NDArray[np.float64]:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def angle_deg(u: Any, v: Any) -> float:
    """Unsigned angle between two directions, degrees."""
    c = float(np.clip(np.dot(_unit(u), _unit(v)), -1.0, 1.0))
    return math.degrees(math.acos(c))


def signed_angle_deg(u: Any, v: Any, about: Any) -> float:
    """Angle from u to v about `about` (right hand), degrees in (-180, 180]."""
    u, v, about = _unit(u), _unit(v), _unit(about)
    return math.degrees(math.atan2(float(np.dot(np.cross(u, v), about)), float(np.dot(u, v))))


def reduce_angle(a: float, symmetry: int | None) -> float:
    """`a` modulo the rotations a symmetric feature cannot tell apart, in (-180/n, 180/n]."""
    period = 360.0 / (symmetry or 1)
    r = math.fmod(a, period)
    if r > period / 2:
        r -= period
    elif r <= -period / 2:
        r += period
    return r


def distance_to_line(p: Any, point: Any, direction: Any) -> float:
    d = np.asarray(p, dtype=float) - np.asarray(point, dtype=float)
    u = _unit(direction)
    return float(np.linalg.norm(d - u * np.dot(d, u)))


def along(p: Any, point: Any, direction: Any) -> float:
    return float(np.dot(np.asarray(p, dtype=float) - np.asarray(point, dtype=float), _unit(direction)))


def rotation_about(point: Any, axis: Any, degrees: float) -> Frame:
    """The rigid motion turning space by `degrees` about the line through `point` along `axis`."""
    u = _unit(axis)
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    x, y, z = u
    r = np.array([
        [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
        [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
    ])  # fmt: skip
    m = np.eye(4)
    m[:3, :3] = r
    p = np.asarray(point, dtype=float)
    m[:3, 3] = p - r @ p
    return m


def translation(v: Any) -> Frame:
    m = np.eye(4)
    m[:3, 3] = np.asarray(v, dtype=float)
    return m


def flip_x() -> Frame:
    """Half a turn about X: the frame a mating connector has when it faces this one."""
    return np.diag([1.0, -1.0, -1.0, 1.0])
