"""Closed loops of motion: what a loop solver is given, what every position it gives must hold, and the protocol a
solver plugin meets.

A mate in a closed loop of mates, or coupled by a relation to parts that cannot follow it, moves only with the rest
of its loop. Motion analysis sweeps such a loop by asking the configured solver (`hitlcad_solvers`,
`Settings.solver`) for its positions along one driving mate, and checks every position here: a solver proposes,
hitlcad checks. A position is accepted when

- each mate holds what its type `holds` of b's frame in a's, as assembled, within the verification tolerances;
- each relation holds b = `ratio` × a along its `coordinates`, from as assembled, turns counted on past a half turn;
- the driving mate sits where it was asked to;
- no point has moved more than twice `SAMPLE_MM` since the position before, so the positions show the motion and a
  jump to the loop's other way of closing is refused.

A position is the motion of each moving cluster from where it was assembled; the reference (the cluster the loop
hangs from, ground or the part nearest it) does not move.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

import numpy as np

from hitlcad.core import frames as fr
from hitlcad.core.structure import Mate, Relation, Tolerances

SAMPLE_MM = 0.5  # the furthest any point moves between two positions a solver gives, as it aims
_WEAK = 1e-3  # how hard what no mate holds (a shaft's slide in its bore) is kept where it was
_PARTS = ("origin_xy", "origin_z", "axis", "clocking")
_RANK = 1e-7  # singular values below this, × the largest, are none


@dataclass(frozen=True)
class LoopMate:
    """A moving mate of the loop: the clusters and frames of its ends as assembled, b's as the mate compares it
    (`Mate.facing`), and what it holds (its type's, less clocking where the connectors are round about the axis)."""

    label: str
    mate: Mate
    a: str
    b: str
    fa: fr.Frame
    fb: fr.Frame
    holds: frozenset[str]


@dataclass(frozen=True)
class LoopRelation:
    """A relation of the loop: its ends' clusters and frames as assembled, its ratio, the kinds of a's and b's
    coordinates ("angle" or "travel") and how far from holding it may be (mm)."""

    label: str
    relation: Relation
    a: str
    b: str
    fa: fr.Frame
    fb: fr.Frame
    ratio: float
    kinds: tuple[str, str]
    tolerance: float


@dataclass(frozen=True)
class Position:
    """Where a loop is: the driving mate's value, each moving cluster's motion from as assembled, and every
    coordinate the loop has (each mate's angle and travel, each relation's a and b) as last measured (`raw`) and
    counted on from as assembled (`total`)."""

    value: float
    poses: dict[str, fr.Frame]
    raw: tuple[float, ...]
    total: tuple[float, ...]


@dataclass
class LoopProblem:
    """A loop to move: its moving clusters, its mates and relations, the mate that drives it and along what, the
    points that bound each moving cluster (its leaves' box corners, as assembled), and the tolerances."""

    clusters: list[str]
    mates: list[LoopMate]
    relations: list[LoopRelation]
    driver: int
    kind: str
    corners: dict[str, np.ndarray]
    tolerances: Tolerances

    @cached_property
    def size(self) -> float:
        """How far the moving clusters reach from their middle (mm): what turns are weighed against."""
        points = np.vstack(list(self.corners.values()))
        return max(float(np.linalg.norm(points - points.mean(axis=0), axis=1).max()), 1.0)

    @cached_property
    def centres(self) -> dict[str, np.ndarray]:
        return {c: points.mean(axis=0) for c, points in self.corners.items()}

    @cached_property
    def _q0(self) -> list[fr.Frame]:
        return [np.linalg.inv(m.fa) @ m.fb for m in self.mates]

    @cached_property
    def _periods(self) -> list[bool]:
        """Which coordinates are turns, counted on past a half turn."""
        return [True, False] * len(self.mates) + [k == "angle" for r in self.relations for k in r.kinds]

    @property
    def _driven(self) -> int:
        return 2 * self.driver + (0 if self.kind == "angle" else 1)

    def _scale(self, kind: str) -> float:
        """mm per unit of a coordinate: a degree at the loop's size, or a mm."""
        return math.radians(self.size) if kind == "angle" else 1.0

    # ------------------------------------------------------------------ positions

    def start(self) -> Position:
        """Where the loop sits as assembled."""
        poses = {c: fr.IDENTITY for c in self.clusters}
        raw = self._raw(poses, self._seen(poses))
        return Position(raw[self._driven], poses, raw, raw)

    def moved(self, x: np.ndarray, around: Position) -> dict[str, fr.Frame]:
        """The poses `x` gives from `around`: per cluster a turn (its rotation vector × `size`) about its middle,
        then a shift (mm). A solver need not use it."""
        out = {}
        for i, c in enumerate(self.clusters):
            pose = around.poses[c]
            centre = pose[:3, :3] @ self.centres[c] + pose[:3, 3]
            step = np.eye(4)
            step[:3, :3] = _turn(x[6 * i : 6 * i + 3] / self.size)
            step[:3, 3] = centre - step[:3, :3] @ centre + x[6 * i + 3 : 6 * i + 6]
            out[c] = step @ pose
        return out

    def travel(self, a: dict[str, fr.Frame], b: dict[str, fr.Frame]) -> float:
        """The furthest any point of a moving cluster moves from poses `a` to poses `b` (mm)."""
        out = 0.0
        for c, points in self.corners.items():
            pa, pb = (points @ p[:3, :3].T + p[:3, 3] for p in (a[c], b[c]))
            out = max(out, float(np.linalg.norm(pb - pa, axis=1).max()))
        return out

    def residuals(self, poses: dict[str, fr.Frame], value: float, before: Position) -> np.ndarray:
        """How far `poses` are from a position with the driver at `value` (mm): what the mates hold and the
        relations, from as assembled; the driver; and, weakly, what nothing holds, from `before`."""
        hard, driven, total = self._held(poses, before)
        weak = []
        seen_before = self._seen(before.poses)
        for i, (m, q, q1) in enumerate(zip(self.mates, self._seen(poses), seen_before, strict=True)):
            free = [p for p in _PARTS if p not in m.holds and not (i == self.driver and p == _along(self.kind))]
            weak += [_WEAK * _part(p, q, q1, self.size) for p in free]
        off = (driven - value) * self._scale(self.kind)
        return np.concatenate([hard, [off], *weak])

    def check(self, poses: dict[str, fr.Frame], value: float, before: Position) -> Position | str:
        """`poses` as the position with the driver at `value` after `before`, or why they are not one."""
        tol = self.tolerances
        seen = self._seen(poses)
        for m, q, q0 in zip(self.mates, seen, self._q0, strict=True):
            for p in sorted(m.holds):
                off = float(np.abs(_part(p, q, q0, 1.0)).max())
                if p.startswith("origin") and off > tol.position_mm:
                    return f"{m.label} is off by {off:.3f} mm"
                if not p.startswith("origin") and math.degrees(off) > tol.angle_deg:
                    return f"{m.label} is off by {math.degrees(off):.3f}°"
        raw = self._raw(poses, seen)
        total = self._count(raw, before)
        for j, r in enumerate(self.relations):
            qa, qb = total[2 * len(self.mates) + 2 * j : 2 * len(self.mates) + 2 * j + 2]
            off = abs(qb - r.ratio * qa) * self._scale(r.kinds[1])
            if off > r.tolerance:
                return f"{r.label} is off by {off:.3f} mm"
        if abs(total[self._driven] - value) * self._scale(self.kind) > tol.position_mm:
            return f"{self.mates[self.driver].label} is not at {value:g}"
        if (moved := self.travel(before.poses, poses)) > 2 * SAMPLE_MM:
            return f"it moved {moved:.2f} mm at once"
        return Position(value, poses, tuple(raw), tuple(total))

    def coordinate(self, position: Position, mate: int, kind: str) -> float:
        """A mate's angle or travel at a position, counted on from as assembled."""
        return position.total[2 * mate + (0 if kind == "angle" else 1)]

    # ------------------------------------------------------------------ freedoms

    def freedoms(self) -> tuple[bool, int]:
        """Whether the driver can move the loop where it sits, and how many more ways it can move with the
        driver held: what a part can do alone about or along an axis it is mated on (a shaft turning or sliding
        in its bore) without moving anything else is not one."""
        at = self.start()
        n = 6 * len(self.clusters)

        def rows(x: np.ndarray) -> np.ndarray:
            hard, driven, _ = self._held(self.moved(x, at), at)
            return np.concatenate([hard, [driven * self._scale(self.kind)]])

        h = 1e-6
        base = rows(np.zeros(n))
        jac = np.column_stack([(rows(h * e) - base) / h for e in np.eye(n)])
        held, with_driver = _rank(jac[:-1]), _rank(jac)
        idle = [v for v in self._alone() if np.linalg.norm(jac @ v) <= 1e-6 * np.linalg.norm(v)]
        return with_driver > held, max(n - with_driver - _rank(np.array(idle)) if idle else n - with_driver, 0)

    def _alone(self) -> list[np.ndarray]:
        """A turn about and a slide along each axis a moving cluster is mated on, of that cluster alone (as `x`)."""
        out = []
        for i, c in enumerate(self.clusters):
            for m in self.mates:
                if c not in (m.a, m.b):
                    continue
                point, axis = fr.origin(m.fa), fr.z_axis(m.fa)
                for turn, slide in ((axis, np.cross(axis, self.centres[c] - point)), (np.zeros(3), axis)):
                    v = np.zeros(6 * len(self.clusters))
                    v[6 * i : 6 * i + 6] = [*(turn * self.size), *slide]
                    out.append(v)
        return out

    # ------------------------------------------------------------------ measuring

    def _seen(self, poses: dict[str, fr.Frame]) -> list[fr.Frame]:
        """Each mate's b frame, seen in its a frame."""
        return [np.linalg.inv(poses.get(m.a, fr.IDENTITY) @ m.fa) @ (poses.get(m.b, fr.IDENTITY) @ m.fb)
                for m in self.mates]  # fmt: skip

    def _raw(self, poses: dict[str, fr.Frame], seen: list[fr.Frame]) -> tuple[float, ...]:
        out: list[float] = []
        for q in seen:
            out += [math.degrees(math.atan2(q[1, 0], q[0, 0])), float(q[2, 3])]
        for r in self.relations:
            out += r.relation.coordinates(r.fa, r.fb, poses.get(r.a, fr.IDENTITY) @ r.fa,
                                          poses.get(r.b, fr.IDENTITY) @ r.fb)  # fmt: skip
        return tuple(out)

    def _count(self, raw: tuple[float, ...], before: Position) -> tuple[float, ...]:
        """Turns counted on from `before`'s (each step turns less than half a turn); travels as they are."""
        return tuple(t + fr.reduce_angle(r - r0, None) if turn else r
                     for r, r0, t, turn in zip(raw, before.raw, before.total, self._periods, strict=True))  # fmt: skip

    def _held(self, poses: dict[str, fr.Frame], before: Position) -> tuple[np.ndarray, float, tuple[float, ...]]:
        """What the mates and relations hold, as residuals (mm); the driver's value; every coordinate."""
        seen = self._seen(poses)
        total = self._count(self._raw(poses, seen), before)
        rows = [_part(p, q, q0, self.size) for m, q, q0 in zip(self.mates, seen, self._q0, strict=True)
                for p in _PARTS if p in m.holds]  # fmt: skip
        for j, r in enumerate(self.relations):
            qa, qb = total[2 * len(self.mates) + 2 * j : 2 * len(self.mates) + 2 * j + 2]
            rows.append(np.array([(qb - r.ratio * qa) * self._scale(r.kinds[1])]))
        return np.concatenate(rows) if rows else np.zeros(0), total[self._driven], total


class Solver(Protocol):
    """A loop solver, contributed through `hitlcad_solvers` by name. It proposes positions; `LoopProblem.check`
    decides which are."""

    name: str
    version: str  # part of what a sweep is cached by, and of what a human's watch of it is for

    def supports(self, problem: LoopProblem) -> str:
        """Why it cannot move this loop (a relation type it does not know), or ''."""

    def trace(self, problem: LoopProblem, start: Position, end: float, budget: int) -> Iterator[Position]:
        """Positions from `start` towards the driver at `end`, each `problem.check`'s, until it gets there or can
        go no further, at most `budget` of them."""


def _along(kind: str) -> str:
    """The part of b's frame a mate's free motion moves."""
    return "clocking" if kind == "angle" else "origin_z"


def _part(part: str, q: fr.Frame, ref: fr.Frame, size: float) -> np.ndarray:
    """How a part of b's frame seen in a's (`q`) differs from `ref`'s: mm, or radians × `size`."""
    if part == "origin_xy":
        return q[:2, 3] - ref[:2, 3]
    if part == "origin_z":
        return q[2:3, 3] - ref[2:3, 3]
    if part == "axis":
        return (q[:2, 2] - ref[:2, 2]) * size
    turned = math.atan2(q[1, 0], q[0, 0]) - math.atan2(ref[1, 0], ref[0, 0])
    return np.array([math.remainder(turned, math.tau) * size])


def _turn(w: np.ndarray) -> np.ndarray:
    """The rotation by rotation vector `w` (radians)."""
    k = np.array([[0.0, -w[2], w[1]], [w[2], 0.0, -w[0]], [-w[1], w[0], 0.0]])
    a = float(np.linalg.norm(w))
    if a < 1e-8:
        return np.eye(3) + k + k @ k / 2
    return np.eye(3) + math.sin(a) / a * k + (1 - math.cos(a)) / a**2 * k @ k


def _rank(m: np.ndarray) -> int:
    if m.size == 0:
        return 0
    s = np.linalg.svd(m, compute_uv=False)
    return int((s > _RANK * max(float(s[0]), 1.0)).sum())
