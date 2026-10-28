"""The loop sweep: a loop's positions from the loop solver, each checked, and every pair of instances its motion may
bring together measured at each of them.

One of the loop's mates drives it: the first with `limits`, through them; else the first whose full turn the solver
completes; else the first. The solver (`AnalysisSettings.loop_solver`) traces the loop from where it sits towards
each end of that range, and every position it gives is checked again (`LoopProblem.check`), so a solver can stop
short but not pass what does not hold. Where a way ends short of its end, the loop goes no further (it locks at a
dead point).

A position is clear when no pair of instances it moves overlaps there beyond what they share where they sit. A pair
is measured again only once the positions since have moved its points as far as its gap was, so every position is
shown clear; one no clearance is asked of whose gap is less than a position's move (gears in mesh) is from then on
only tested for meeting at each position (`Collider.meets`), far cheaper than measuring it. What happens between
two positions is not shown clear, and the result says so (`motion.sampled`) until the human has watched the motion.
A sweep is cached twice over: its solve by what that depends on (the loop's mates, relations, frames and bounds, the
driver, the solver and the tolerances), and its collisions by that and every pair measured; the second key is also
what a human's watch is of.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from pydantic import Field

from hitlcad.analysis.base import ResultCache
from hitlcad.analysis.interference import boxes_touch
from hitlcad.analysis.model import AnalysisSettings
from hitlcad.analysis.motion.findings import Contact, Findings, Least, Pair, Parted, at_rest
from hitlcad.analysis.motion.plans import LoopPlan, link_label, ranges
from hitlcad.core import frames as fr
from hitlcad.core.loops import SAMPLE_MM, LoopMate, LoopProblem, LoopRelation, Position, Solver
from hitlcad.core.structure import Axis, Mate, Relation, symmetry_of
from hitlcad.geometry.mesh import Collider
from hitlcad.kernel.model import Model

if TYPE_CHECKING:
    import manifold3d


class LoopStop(Model):
    """Where one way along the driver's range ended before its end: the loop goes no further there, or (`why`)
    the solver's next position does not hold."""

    at: float
    end: float
    why: str = ""


class Solved(Model):
    """A loop's positions along its driving mate, each checked: what it moved, how far each way it went, and what
    each of its mates moved through."""

    driver: int = Field(description="Which of the plan's links drives it")
    kind: str
    rest: float = Field(description="The driver's value where the loop sits")
    held: bool = Field(default=False, description="The rest of the loop holds the driver: nothing moves")
    freedoms: int = Field(default=0, description="More ways it can move with the driver held, held where they sit")
    clusters: list[str] = Field(default_factory=list, description="What moves, in the order of `poses`")
    values: list[float] = Field(default_factory=list, description="The driver at each position, ascending")
    poses: list[list[list[float]]] = Field(
        default_factory=list, description="Per position, each moving cluster's motion from as assembled, 3 × 4 flat"
    )
    twists: list[list[list[float]]] = Field(
        default_factory=list,
        description="Per position, each moving cluster's turn (rad) and shift (mm) per unit of the driver",
    )
    spans: list[dict[str, tuple[float, float]]] = Field(
        default_factory=list, description="Per mate of the plan, each of its free motions' least and most"
    )
    stops: list[LoopStop] = Field(default_factory=list)
    step_mm: float = Field(default=0.0, description="The furthest any point moved between two positions")
    full_turn: bool = False
    key: str = Field(default="", description="The solve's: what it depends on")

    @property
    def rest_index(self) -> int:
        return self.values.index(self.rest)

    def stacked(self) -> dict[str, np.ndarray]:
        """Each moving cluster's motion at every position, as a stack of frames."""
        flat = np.asarray(self.poses, dtype=float).reshape(len(self.values), len(self.clusters), 3, 4)
        frames = np.concatenate([flat, np.broadcast_to([[[[0.0, 0.0, 0.0, 1.0]]]], (*flat.shape[:2], 1, 4))], axis=2)
        return {c: frames[:, k] for k, c in enumerate(self.clusters)}

    @property
    def locks(self) -> list[LoopStop]:
        """The ways it ended short because the loop goes no further."""
        return [s for s in self.stops if not s.why]


class LoopSwept(Findings):
    """What a loop's sweep found: why it was not swept, or its solved positions and what meets at them."""

    why: str = Field(default="", description="Why it was not swept: no solver, or one that cannot move it")
    solved: Solved | None = None
    key: str = Field(default="", description="The sweep's: what a human's watch of it is of")


@dataclass
class LoopPair(Pair):
    """A pair, the moving cluster each rides on (`hq` None: q stays), and the turn p makes against q, where a mate
    between their clusters holds them to one (a carrier in its bearing): what is round about it of either is left
    out (`turning.not_round`), as the exact sweep leaves it out. Once shaped, the solids it moves, and their
    colliders."""

    hp: str
    hq: str | None
    turn: Axis | None
    a: manifold3d.Manifold | None = None
    b: manifold3d.Manifold | None = None
    ca: Collider | None = None
    cb: Collider | None = None

    def key(self) -> list[object]:
        turn = (
            None
            if self.turn is None
            else [*np.round(self.turn.origin, 6).tolist(), *np.round(self.turn.direction, 6).tolist()]
        )
        return [self.p.path, self.p.part.digest, np.round(self.p.transform, 6).tolist(), self.hp, self.q.path,
                self.q.part.digest, np.round(self.q.transform, 6).tolist(), self.hq, turn, self.need, self.tracked,
                round(self.sitting_mm3, 6), self.contact_key()]  # fmt: skip

    def against(self, poses: dict[str, np.ndarray]) -> np.ndarray:
        """p's motion against q (in q's frame as assembled) at each position (`Solved.stacked`)."""
        moved = poses[self.hp]
        return moved if self.hq is None else fr.rigid_inverse(poses[self.hq]) @ moved


REPORTED = 400  # the most positions of a loop a report carries, for the panel


def sweep(plan: LoopPlan, settings: AnalysisSettings, cache: ResultCache) -> LoopSwept:
    """The loop swept: once per plan, and cached by what it depends on."""
    if plan.swept is None:
        plan.swept = _sweep(plan, settings, cache)
    return plan.swept


def _sweep(plan: LoopPlan, settings: AnalysisSettings, cache: ResultCache) -> LoopSwept:
    solver = settings.loop_solver
    if solver is None:
        why = f"no loop solver {settings.solver!r} is installed" if settings.solver else "no loop solver is set"
        return LoopSwept(why=why)
    solved = _drive(plan, solver, cache)
    if isinstance(solved, str):
        return LoopSwept(why=solved)
    out = LoopSwept(solved=solved)
    if solved.held or len(solved.values) < 2:
        return out
    pairs = at_rest(_pairs(plan, solved), plan.world, settings, cache, out)
    key = cache.key("loop-sweep", solved.key, [p.key() for p in pairs], settings.overlap_mm3,
                    settings.mesh_tolerance_mm, plan.world.tolerances.position_mm)  # fmt: skip
    found = Findings.model_validate(cache.get_or(key, lambda: _collide(pairs, solved, plan, settings).model_dump()))
    return out.model_copy(update={"contacts": found.contacts, "parted": found.parted, "least": found.least, "key": key})


def _drive(plan: LoopPlan, solver: Solver, cache: ResultCache) -> Solved | str:
    """The loop solved along its driver (see the module's docstring), or why the solver cannot move it."""
    driven = [(i, kind, lo, hi, cont) for i, k in enumerate(plan.links) for kind, lo, hi, cont in ranges(k.source)]
    limited = [d for d in driven if not d[4]]
    tries = limited[:1] or [d for d in driven if d[4]]
    first: Solved | None = None
    for i, kind, lo, hi, continuous in tries:
        problem = _problem(plan, i, kind)
        if why := solver.supports(problem):
            return f"the loop solver {solver.name} cannot move it: {why}"
        rest = problem.start().value
        ends = [rest + 180.0, rest - 180.0] if continuous else [hi, lo]
        key = _solve_key(problem, solver, cache)
        solved = Solved.model_validate(
            cache.get_or(key, lambda p=problem, e=ends, c=continuous: _solve(p, solver, e, c).model_dump())
        ).model_copy(update={"key": key})
        first = first or solved
        if solved.held or not continuous or solved.full_turn:
            return solved
    return first or "none of its mates has a range of motion"


# --------------------------------------------------------------------------- the problem


def _problem(plan: LoopPlan, driver: int, kind: str) -> LoopProblem:
    find, tol = plan.world.clusters.find, plan.world.tolerances
    mates = []
    for k in plan.links:
        mate: Mate = k.source
        holds = set(mate.holds)
        if symmetry_of(k.a.spec.symmetry, k.b.spec.symmetry) == "axial":
            holds.discard("clocking")
        mates.append(LoopMate(link_label(k), mate, find(k.a.instance), find(k.b.instance), k.a.frame,
                              mate.facing(k.b.frame), frozenset(holds)))  # fmt: skip
    relations = []
    for k in plan.relations:
        rel: Relation = k.source
        axes = rel.axes(k.a.frame, k.b.frame)
        relations.append(LoopRelation(link_label(k), rel, find(k.a.instance), find(k.b.instance), k.a.frame, k.b.frame,
                                      plan.world.laws[id(k)], (axes[0].kind, axes[1].kind),
                                      rel.tolerance or tol.position_mm))  # fmt: skip
    leaves = plan.world.leaves
    corners = {c: np.vstack([_corners(leaves[p].bounds) for p in plan.leaves_of(c)]) for c in plan.clusters}
    return LoopProblem(plan.clusters, mates, relations, driver, kind, corners, tol)


def _corners(bounds: np.ndarray) -> np.ndarray:
    return np.array([[x, y, z] for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]])


def _solve_key(problem: LoopProblem, solver: Solver, cache: ResultCache) -> str:
    """What a loop's solve depends on, as plain values: never an object, whose text may carry a build's path."""

    def rounded(f: np.ndarray) -> list[Any]:
        return np.round(f, 6).tolist()

    tol = problem.tolerances
    return cache.key(
        "loop-solve", solver.name, solver.version, SAMPLE_MM, tol.position_mm, tol.angle_deg, tol.motion_steps,
        [[m.label, m.mate.model_dump(mode="json"), m.a, m.b, rounded(m.fa), rounded(m.fb), sorted(m.holds)]
         for m in problem.mates],
        [[r.label, r.relation.model_dump(mode="json"), r.a, r.b, rounded(r.fa), rounded(r.fb), r.law.key(),
          r.kinds, r.tolerance] for r in problem.relations],
        problem.clusters, problem.driver, problem.kind, {c: rounded(p) for c, p in problem.corners.items()},
    )  # fmt: skip


def _solve(problem: LoopProblem, solver: Solver, ends: list[float], continuous: bool) -> Solved:
    """The loop's positions each way from where it sits, as far as it goes, each checked."""
    start = problem.start()
    moves, freedoms = problem.freedoms()
    out = Solved(driver=problem.driver, kind=problem.kind, rest=start.value, freedoms=freedoms,
                 clusters=problem.clusters)  # fmt: skip
    if not moves:
        return out.model_copy(update={"held": True})
    ways = []
    for end in ends:
        found, before, why = [], start, ""
        for given in solver.trace(problem, start, end, problem.tolerances.motion_steps):
            checked = problem.check(given.poses, given.value, before)
            if isinstance(checked, str):
                why = f"the solver's position at {given.value:.2f} does not hold: {checked}"
                break
            found.append(checked)
            before = checked
        if not math.isclose(before.value, end, abs_tol=1e-6):
            out.stops.append(LoopStop(at=before.value, end=end, why=why))
        ways.append(found)
    up, down = ways if ends[0] > ends[1] else ways[::-1]
    positions = [*reversed(down), start, *up]
    steps = [problem.travel(a.poses, b.poses) for a, b in zip(positions, positions[1:], strict=False)]
    spans = []
    for i, m in enumerate(problem.mates):
        moved = {k: [problem.coordinate(p, i, k) for p in positions] for k in m.mate.free_motion}
        spans.append({k: (min(v), max(v)) for k, v in moved.items()})
    return out.model_copy(update={
        "values": [p.value for p in positions],
        "poses": [[np.round(p.poses[c][:3], 9).reshape(-1).tolist() for c in problem.clusters] for p in positions],
        "twists": _twists(problem, positions),
        "spans": spans,
        "step_mm": max(steps, default=0.0),
        "full_turn": continuous and not out.stops,
    })  # fmt: skip


def _twists(problem: LoopProblem, positions: list[Position]) -> list[list[list[float]]]:
    """Each moving cluster's velocity per unit of the driver at each position, from its neighbours."""
    out = []
    for i in range(len(positions)):
        a, b = positions[max(i - 1, 0)], positions[min(i + 1, len(positions) - 1)]
        span = b.value - a.value
        row = []
        for c in problem.clusters:
            step = b.poses[c] @ np.linalg.inv(a.poses[c])
            row.append([*(_rotation_vector(step[:3, :3]) / span), *(step[:3, 3] / span)] if span else [0.0] * 6)
        out.append(row)
    return out


def _rotation_vector(r: np.ndarray) -> np.ndarray:
    angle = math.acos(max(-1.0, min(1.0, (float(np.trace(r)) - 1) / 2)))
    axis = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]])
    return axis / 2 if angle < 1e-9 else axis * angle / (2 * math.sin(angle))


# --------------------------------------------------------------------------- collisions


def _pairs(plan: LoopPlan, solved: Solved) -> list[LoopPair]:
    """Each leaf the loop moves with every other leaf its motion may bring it near, but those that move with it."""
    w, tol = plan.world, plan.world.tolerances
    hosts = {p: c for c in plan.clusters for p in plan.leaves_of(c)}
    poses = solved.stacked()
    boxes = {}
    for p, c in hosts.items():
        moved = _placed(_corners(w.leaves[p].bounds), poses[c]).reshape(-1, 3)
        boxes[p] = np.array([moved.min(axis=0), moved.max(axis=0)])
    turns = _turns(plan, solved)
    out = []
    for p, hp in hosts.items():
        for q, other in w.leaves.items():
            hq = hosts.get(q)
            if q == p or hq == hp or (hq is not None and q < p):
                continue
            need = w.tracked.get(frozenset((p, q)))
            if boxes_touch(boxes[p], other.bounds if hq is None else boxes[q], (need or 0.0) + tol.position_mm):
                turn = turns.get(frozenset((hp, hq or w.clusters.find(q))))
                out.append(LoopPair(w.leaves[p], other, need or 0.0, need is not None, hp, hq, turn))
    return out


def _turns(plan: LoopPlan, solved: Solved) -> dict[frozenset[str], Axis]:
    """The turn one of two clusters makes against the other where a mate between them holds it to one: the mate
    holds its axis, and its travel stays put through the positions."""
    find, tol = plan.world.clusters.find, plan.world.tolerances
    out = {}
    for k, span in zip(plan.links, solved.spans, strict=True):
        if "angle" not in span or not {"origin_xy", "axis"} <= k.source.holds:
            continue
        lo, hi = span.get("travel", (0.0, 0.0))
        if hi - lo <= tol.position_mm:
            out[frozenset((find(k.a.instance), find(k.b.instance)))] = Axis(
                "angle", fr.origin(k.a.frame), fr.z_axis(k.a.frame)
            )
    return out


def _collide(pairs: list[LoopPair], solved: Solved, plan: LoopPlan, settings: AnalysisSettings) -> Findings:
    """Each pair, p moved against q, measured at the positions each way from where the loop sits, up to the first
    contact each way."""
    tol, out = plan.world.tolerances, Findings()
    round_mm = settings.mesh_tolerance_mm + tol.position_mm  # a facetted round is round to this
    kept = []
    for x in pairs:
        if x.turn is None:
            (x.a, x.ca), (x.b, x.cb) = (x.p.manifold, x.p.collider), (x.q.manifold, x.q.collider)
        else:
            (x.a, x.ca), (x.b, x.cb) = x.p.not_round(x.turn, round_mm), x.q.not_round(x.turn, round_mm)
            if x.ca is None or x.cb is None:  # all of one is round about the turn: it keeps its gap
                continue
            x.sitting_mm3 = float((x.a ^ x.b).volume()) if x.sitting_mm3 else 0.0
        kept.append(x)
    least: dict[tuple[str, str], Least] = {}
    poses, rest = solved.stacked(), solved.rest_index
    along = {id(x): x.against(poses) for x in kept}
    for way in (range(rest, len(solved.values)), range(rest, -1, -1)):
        steps = list(way)
        if len(steps) < 2:
            continue
        moves = {id(x): along[id(x)][steps] for x in kept}
        travel = {id(x): _travel(_corners(x.p.bounds), moves[id(x)]) for x in kept}
        due = {id(x): 0.0 for x in kept}
        need = {id(x): x.need for x in kept}
        near: set[int] = set()  # no clearance asked of it, and its gap skips no position: now only whether it meets
        hits: list[Contact] = []
        parted: list[Parted] = []
        for j, i in enumerate(steps):
            for x in kept:
                assert x.a is not None and x.b is not None and x.ca is not None and x.cb is not None
                so_far = travel[id(x)][j]
                if x.touching is not None:  # kept in contact: measured at every position, for parting or overlap
                    found = x.ca.distance(x.cb, moves[id(x)][j])
                    if found > x.parts_at(settings, tol):
                        parted.append(Parted(kind=solved.kind, at=solved.values[i], end=solved.values[steps[-1]],
                                             a=x.p.path, b=x.q.path, gap_mm=round(found, 6),
                                             relation=x.touching.source.address))  # fmt: skip
                        continue
                elif so_far < due[id(x)]:
                    continue
                elif id(x) in near:
                    found = 0.0 if x.ca.meets(x.cb, moves[id(x)][j]) else tol.position_mm
                else:
                    found = x.ca.distance(x.cb, moves[id(x)][j])
                    if found >= need[id(x)] + travel[id(x)][-1] - so_far + tol.position_mm:  # it cannot close the gap
                        due[id(x)] = math.inf
                        continue
                    if x.tracked:
                        _record(least, x, found, solved.kind, solved.values[i])
                    need[id(x)] = need[id(x)] if found >= need[id(x)] else 0.0  # its clearance is broken: only contact
                    then = travel[id(x)][j + 1] if j + 1 < len(steps) else math.inf
                    if not x.tracked and so_far + found < then:
                        near.add(id(x))
                shared = (
                    float((x.a.transform(moves[id(x)][j][:3, :]) ^ x.b).volume()) if found < tol.position_mm else 0.0
                )
                if shared > x.sitting_mm3 + settings.overlap_mm3:
                    clear = solved.values[steps[j - 1]] if j else solved.values[i]
                    hits.append(Contact(kind=solved.kind, at=solved.values[i], end=solved.values[steps[-1]],
                                        clear=clear, a=x.p.path, b=x.q.path, overlap_mm3=round(shared, 6)))  # fmt: skip
                due[id(x)] = so_far + max(found - need[id(x)], tol.position_mm)
            if hits or parted:
                out.contacts += hits
                out.parted += parted
                break
    out.least = list(least.values())
    return out


def _placed(points: np.ndarray, motions: np.ndarray) -> np.ndarray:
    """Points (n × 3) moved by each of a stack of motions: positions × n × 3."""
    return np.einsum("sij,nj->sni", motions[:, :3, :3], points) + motions[:, None, :3, 3]


def _travel(corners: np.ndarray, motions: np.ndarray) -> list[float]:
    """How far points have moved, summed over the motions so far, at each one."""
    steps = np.linalg.norm(np.diff(_placed(corners, motions), axis=0), axis=2).max(axis=1)
    return np.concatenate([[0.0], np.cumsum(steps)]).tolist()


def _record(least: dict[tuple[str, str], Least], pair: LoopPair, found: float, kind: str, at: float) -> None:
    key = (pair.p.path, pair.q.path)
    if key not in least or found < least[key].gap_mm:
        least[key] = Least(a=pair.p.path, b=pair.q.path, gap_mm=round(found, 6), kind=kind, at=round(at, 6),
                           complete=False)  # fmt: skip
