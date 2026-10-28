"""Closed loops of motion: what a loop solver is given, what every position it gives must hold, and the protocol a
solver plugin meets.

A mate in a closed loop of mates, or coupled by a relation to parts that cannot follow it, moves only with the rest
of its loop. Motion analysis sweeps such a loop by asking the configured solver (`hitlcad_solvers`,
`Settings.solver`) for its positions along one driving mate, and checks every position here: a solver proposes,
hitlcad checks. A position is accepted when

- each mate holds what its type `holds` of b's frame in a's, as assembled, within the verification tolerances;
- each relation holds b = `law`(a) along its `coordinates`, from as assembled, turns counted on past a half turn;
- the driving mate sits where it was asked to;
- no point has moved more than twice `SAMPLE_MM` since the position before, so the positions show the motion and a
  jump to the loop's other way of closing is refused.

A position is the motion of each moving cluster from where it was assembled; the reference (the cluster the loop
hangs from, ground or the part nearest it) does not move. What a loop holds is one CasADi graph (`LoopProblem.nlp`):
a solver minimises its residuals with the exact derivatives CasADi takes of it, and every check measures through it.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from hitlcad.core import frames as fr
from hitlcad.core.laws import Law
from hitlcad.core.structure import Mate, Relation, Tolerances

if TYPE_CHECKING:
    import casadi as ca

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
    """A relation of the loop: its ends' clusters and frames as assembled, its law, the kinds of a's and b's
    coordinates ("angle" or "travel") and how far from holding it may be (mm)."""

    label: str
    relation: Relation
    a: str
    b: str
    fa: fr.Frame
    fb: fr.Frame
    law: Law
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


@dataclass(eq=False)
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
        """Each mate's b frame seen in its a frame, as assembled."""
        return [fr.rigid_inverse(m.fa) @ m.fb for m in self.mates]

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

    # ------------------------------------------------------------------ the graph

    @cached_property
    def _graph(self) -> _Graph:
        import casadi as ca

        n = len(self.clusters)
        x, around = ca.SX.sym("x", 6 * n), ca.SX.sym("around", 12 * n)
        total0, value = ca.SX.sym("total", len(self._periods)), ca.SX.sym("value")
        before = _poses(around, n)
        moved = [
            *(
                _stepped(x[6 * i : 6 * i + 6], p, self.centres[c], self.size)
                for i, (c, p) in enumerate(zip(self.clusters, before[:-1], strict=True))
            ),
            before[-1],
        ]
        seen0, raw0 = self._measure(before)
        seen, raw = self._measure(moved)
        total = [
            t + fr.wrapped(r - r0) if turn else r
            for r, r0, t, turn in zip(raw, raw0, ca.vertsplit(total0), self._periods, strict=True)
        ]
        hard = [row for m, q, q0 in zip(self.mates, seen, self._q0, strict=True) for p in _PARTS if p in m.holds
                for row in _part(p, q, q0, self.size)]  # fmt: skip
        for j, r in enumerate(self.relations):
            qa, qb = total[2 * len(self.mates) + 2 * j : 2 * len(self.mates) + 2 * j + 2]
            hard.append((qb - r.law.symbolic(qa)) * self._scale(r.kinds[1]))
        along = _along(self.kind)
        weak = [_WEAK * row for i, (m, q, q0) in enumerate(zip(self.mates, seen, seen0, strict=True)) for p in _PARTS
                if p not in m.holds and not (i == self.driver and p == along) for row in _part(p, q, q0, self.size)]  # fmt: skip
        driven = total[self._driven] * self._scale(self.kind)
        residual = ca.vertcat(*hard, driven - value * self._scale(self.kind), *weak)
        held = ca.vertcat(*hard, driven)
        flat = ca.vertcat(*(ca.reshape(p[:3, :].T, 12, 1) for p in moved[:-1]))
        return _Graph(
            nlp={"x": x, "p": ca.vertcat(around, total0, value), "f": ca.sumsqr(residual) / 2},
            moved=ca.Function("moved", [x, around], [flat]),
            measure=ca.Function("measure", [around], [ca.vertcat(*(ca.reshape(q.T, 12, 1) for q in seen0)), ca.vertcat(*raw0)]),
            held=ca.Function("held", [x, around, total0], [ca.jacobian(held, x)]),
        )  # fmt: skip

    def _measure(self, poses: list[ca.SX]) -> tuple[list[ca.SX], list[ca.SX]]:
        """Each mate's b frame seen in its a frame (3 × 4), and every coordinate as measured, at `poses` (the
        reference's last)."""
        import casadi as ca

        index = {c: i for i, c in enumerate(self.clusters)}
        ref = len(self.clusters)

        def at(cluster: str, frame: fr.Frame) -> ca.SX:
            return ca.mtimes(poses[index.get(cluster, ref)], ca.DM(frame))

        seen, raw = [], []
        for m in self.mates:
            a, b = at(m.a, m.fa), at(m.b, m.fb)
            turn = a[:3, :3].T
            q = ca.horzcat(turn @ b[:3, :3], turn @ (b[:3, 3] - a[:3, 3]))
            seen.append(q)
            raw += [fr.heading(fr.IDENTITY, q[:3, 0]), q[2, 3]]
        for r in self.relations:
            raw += r.relation.coordinates(r.fa, r.fb, at(r.a, r.fa), at(r.b, r.fb))
        return seen, raw

    def nlp(self) -> dict[str, ca.SX]:
        """What a solver minimises (`f`, half the sum of the residuals' squares, mm²) over `x` (each moving
        cluster's turn about its middle, as Cayley's vector × `size`, then its shift, from the position before)
        given `p` (`parameters`): what the mates and relations hold, from as assembled; the driver at its value;
        and, weakly, what nothing holds, where it was."""
        return self._graph.nlp

    def parameters(self, value: float, before: Position) -> np.ndarray:
        """`nlp`'s `p` for the driver at `value`, from the position `before`."""
        return np.concatenate([_flat(before.poses, self.clusters), before.total, [value]])

    # ------------------------------------------------------------------ positions

    def start(self) -> Position:
        """Where the loop sits as assembled."""
        poses = {c: fr.IDENTITY for c in self.clusters}
        _, raw = self._seen(poses)
        return Position(raw[self._driven], poses, raw, raw)

    def moved(self, x: np.ndarray, around: Position) -> dict[str, fr.Frame]:
        """The poses `x` (as `nlp`'s) gives from `around`."""
        flat = np.asarray(self._graph.moved(x, _flat(around.poses, self.clusters))).reshape(-1, 3, 4)
        return {c: np.vstack([f, [0.0, 0.0, 0.0, 1.0]]) for c, f in zip(self.clusters, flat, strict=True)}

    def travel(self, a: dict[str, fr.Frame], b: dict[str, fr.Frame]) -> float:
        """The furthest any point of a moving cluster moves from poses `a` to poses `b` (mm)."""
        out = 0.0
        for c, points in self.corners.items():
            pa, pb = (points @ p[:3, :3].T + p[:3, 3] for p in (a[c], b[c]))
            out = max(out, float(np.linalg.norm(pb - pa, axis=1).max()))
        return out

    def check(self, poses: dict[str, fr.Frame], value: float, before: Position) -> Position | str:
        """`poses` as the position with the driver at `value` after `before`, or why they are not one."""
        tol = self.tolerances
        seen, raw = self._seen(poses)
        for m, q, q0 in zip(self.mates, seen, self._q0, strict=True):
            for p in sorted(m.holds):
                off = max(abs(v) for v in _part(p, q, q0, 1.0))
                if p.startswith("origin") and off > tol.position_mm:
                    return f"{m.label} is off by {off:.3f} mm"
                if not p.startswith("origin") and math.degrees(off) > tol.angle_deg:
                    return f"{m.label} is off by {math.degrees(off):.3f}°"
        total = self._count(raw, before)
        for j, r in enumerate(self.relations):
            qa, qb = total[2 * len(self.mates) + 2 * j : 2 * len(self.mates) + 2 * j + 2]
            off = abs(qb - r.law.at(qa)) * self._scale(r.kinds[1])
            if off > r.tolerance:
                return f"{r.label} is off by {off:.3f} mm"
        if abs(total[self._driven] - value) * self._scale(self.kind) > tol.position_mm:
            return f"{self.mates[self.driver].label} is not at {value:g}"
        if (moved := self.travel(before.poses, poses)) > 2 * SAMPLE_MM:
            return f"it moved {moved:.2f} mm at once"
        return Position(value, poses, raw, total)

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
        jac = np.asarray(self._graph.held(np.zeros(n), _flat(at.poses, self.clusters), at.total))
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

    def _seen(self, poses: dict[str, fr.Frame]) -> tuple[np.ndarray, tuple[float, ...]]:
        """Each mate's b frame seen in its a frame (3 × 4), and every coordinate as measured."""
        seen, raw = self._graph.measure(_flat(poses, self.clusters))
        return np.asarray(seen).reshape(-1, 3, 4), tuple(np.asarray(raw).ravel().tolist())

    def _count(self, raw: tuple[float, ...], before: Position) -> tuple[float, ...]:
        """Turns counted on from `before`'s (each step turns less than half a turn); travels as they are."""
        return tuple(t + fr.reduce_angle(r - r0, None) if turn else r
                     for r, r0, t, turn in zip(raw, before.raw, before.total, self._periods, strict=True))  # fmt: skip


@dataclass(frozen=True)
class _Graph:
    nlp: dict[str, Any]
    moved: Any  # (x, around) -> the moved poses, 3 × 4 by rows each
    measure: Any  # (poses) -> each mate's b seen in a (3 × 4 by rows each), every coordinate
    held: Any  # (x, around, total) -> the Jacobian of what the mates and relations hold, and the driver


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


def _part(part: str, q: Any, ref: Any, size: float) -> list[Any]:
    """How a part of b's frame seen in a's (`q`, 3 × 4, numbers or symbols) differs from `ref`'s: mm, or radians
    × `size`."""
    if part == "origin_xy":
        return [q[0, 3] - ref[0, 3], q[1, 3] - ref[1, 3]]
    if part == "origin_z":
        return [q[2, 3] - ref[2, 3]]
    if part == "axis":
        return [(q[0, 2] - ref[0, 2]) * size, (q[1, 2] - ref[1, 2]) * size]
    turned = fr.wrapped(fr.heading(fr.IDENTITY, q[:3, 0]) - fr.heading(fr.IDENTITY, ref[:3, 0]))
    return [math.radians(1) * turned * size]


def _poses(flat: ca.SX, n: int) -> list[ca.SX]:
    """`n` poses, 3 × 4 by rows each, as 4 × 4, and the reference's (still) after them."""
    import casadi as ca

    last = ca.DM([[0.0, 0.0, 0.0, 1.0]])
    return [*(ca.vertcat(ca.reshape(flat[12 * i : 12 * i + 12], 4, 3).T, last) for i in range(n)), ca.SX.eye(4)]


def _stepped(x: ca.SX, pose: ca.SX, centre: np.ndarray, size: float) -> ca.SX:
    """`pose` turned by Cayley's rotation of x[:3] / `size` about the cluster's middle, then shifted by x[3:]: a
    rotation for any x, smooth through none."""
    import casadi as ca

    w = x[:3] / size
    k = ca.vertcat(ca.horzcat(0, -w[2], w[1]), ca.horzcat(w[2], 0, -w[0]), ca.horzcat(-w[1], w[0], 0))
    turn = ca.SX.eye(3) + (k + k @ k / 2) / (1 + ca.dot(w, w) / 4)  # (I − K/2)⁻¹ (I + K/2)
    middle = ca.mtimes(pose[:3, :3], ca.DM(centre)) + pose[:3, 3]
    return ca.vertcat(ca.horzcat(turn @ pose[:3, :3], turn @ pose[:3, 3] + middle - turn @ middle + x[3:]),
                      ca.DM([[0.0, 0.0, 0.0, 1.0]]))  # fmt: skip


def _flat(poses: dict[str, fr.Frame], clusters: list[str]) -> np.ndarray:
    """Poses as `nlp` takes them: each 3 × 4 by rows."""
    return np.concatenate([poses[c][:3].reshape(-1) for c in clusters])


def _rank(m: np.ndarray) -> int:
    if m.size == 0:
        return 0
    s = np.linalg.svd(m, compute_uv=False)
    return int((s > _RANK * max(float(s[0]), 1.0)).sum())
