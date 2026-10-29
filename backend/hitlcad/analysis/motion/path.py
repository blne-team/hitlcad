"""An input's motion as positions: each moving cluster's pose at each value of the input. An exact plan's come
straight from its laws, a degree (or half a millimetre) apart; a plan with blocks is traced, the input stepped from
where it sits towards each end, each block settled in turn given what moves before it, every position checked
(`Block.check`), no point moving more than twice `SAMPLE_MM` between two. A step the blocks do not take is halved;
where halving gets nowhere the motion goes no further (it locks, or the solver loses it)."""

from __future__ import annotations

import math
from functools import cached_property

import numpy as np
from pydantic import Field

from hitlcad.analysis.base import ResultCache
from hitlcad.analysis.model import AnalysisSettings
from hitlcad.analysis.motion.plans import Plan, Stage
from hitlcad.core import frames as fr
from hitlcad.core.loops import SAMPLE_MM, Position, Solver
from hitlcad.kernel.model import Model

_FINEST = 1e-3  # of a first step: the smallest step tried before giving up


class Halt(Model):
    """Where a way of a traced motion ended short of its end: it locks there (`why` empty), or did not hold."""

    at: float
    end: float
    why: str = ""


class Traced(Model):
    """An input's positions: its value at each, ascending, and each moving cluster's motion from as assembled."""

    clusters: list[str] = Field(default_factory=list, description="What moves, in the order of `poses`")
    values: list[float] = Field(default_factory=list)
    poses: list[list[list[float]]] = Field(
        default_factory=list, description="Per position, each moving cluster's motion from as assembled, 3 × 4 flat"
    )
    rest: float = 0.0
    halts: list[Halt] = Field(default_factory=list)
    step_mm: float = Field(default=0.0, description="The furthest any point moved between two positions")
    freedoms: int = Field(default=0, description="More ways its blocks can move, held where they sit")
    held: bool = Field(default=False, description="Its blocks hold the input: nothing moves")
    why: str = Field(default="", description="Why it was not traced")
    key: str = Field(default="", description="What it depends on")

    @property
    def rest_index(self) -> int:
        return self.values.index(self.rest)

    @property
    def locks(self) -> list[Halt]:
        """The ways it ended short because the motion goes no further."""
        return [h for h in self.halts if not h.why]

    @cached_property
    def _stack(self) -> dict[str, np.ndarray]:
        flat = np.asarray(self.poses, dtype=float).reshape(len(self.values), len(self.clusters), 3, 4)
        last = np.broadcast_to([[[[0.0, 0.0, 0.0, 1.0]]]], (*flat.shape[:2], 1, 4))
        frames = np.concatenate([flat, last], axis=2)
        frames.setflags(write=False)
        return {c: frames[:, k] for k, c in enumerate(self.clusters)}

    def stacked(self) -> dict[str, np.ndarray]:
        """Each moving cluster's motion at every position, as a stack of frames: made once, read by every check of
        the motion (so none may change it)."""
        return self._stack


def positions(plan: Plan, settings: AnalysisSettings, cache: ResultCache) -> Traced:
    """The plan's positions, once per plan: exact ones a degree (or half a millimetre) apart, or traced (`trace`)."""
    if plan.traced is None:
        plan.traced = trace(plan, settings, cache) if not plan.exact else _exact(plan)
    return plan.traced


def _exact(plan: Plan) -> Traced:
    rest = plan.rest
    lo, hi = (rest - 180.0, rest + 180.0) if plan.continuous else (min(plan.start, rest), max(plan.end, rest))
    n = max(math.ceil((hi - lo) / (1.0 if plan.kind == "angle" else 0.5)), 1)
    values = sorted({*np.linspace(lo, hi, n + 1).tolist(), rest})
    drives = {c: s.drive for s in plan.stages for c in s.clusters if s.drive}
    poses = [[d.at(v - rest)[:3].reshape(-1).tolist() for d in drives.values()] for v in values]
    return Traced(clusters=list(drives), values=values, poses=poses, rest=rest)


def trace(plan: Plan, settings: AnalysisSettings, cache: ResultCache) -> Traced:
    """The plan's positions, traced through its blocks: once per plan, cached by what they depend on."""
    solver = settings.motion_solver
    if solver is None:
        return Traced(why=f"no solver {settings.solver!r} is installed" if settings.solver else "no solver is set")
    blocks = [s.block for s in plan.stages if s.block]
    for block in blocks:
        if why := solver.supports(block):
            return Traced(why=f"the solver {solver.name} cannot settle {', '.join(block.clusters)}: {why}")
    key = _key(plan, solver, cache)
    traced = Traced.model_validate(cache.get_or(key, lambda: _trace(plan, solver).model_dump()))
    return traced.model_copy(update={"key": key})


def _trace(plan: Plan, solver: Solver) -> Traced:
    blocks = [s for s in plan.stages if s.block]
    freedoms = [s.block.freedoms() for s in blocks if s.block]
    out = Traced(clusters=plan.moving, rest=plan.rest, freedoms=sum(f for _, f in freedoms))
    if not all(moves for moves, _ in freedoms):
        return out.model_copy(update={"held": True})
    corners = _Corners({c: plan.world.corners[c] for c in plan.moving})
    reach = float(np.linalg.norm(corners.points - fr.origin(plan.link.a.frame), axis=1).max())
    first = SAMPLE_MM if plan.kind == "travel" else math.degrees(SAMPLE_MM / max(reach, 1.0))
    start = {id(s): s.block.start for s in blocks if s.block}
    still = dict.fromkeys(plan.moving, fr.IDENTITY)
    ways: list[list[tuple[float, dict[str, fr.Frame]]]] = []
    for end in plan.ends():
        sign, found = math.copysign(1.0, end - plan.rest), []
        value, poses, at, step = plan.rest, still, dict(start), first
        rates = {id(s): np.zeros(6 * len(s.clusters)) for s in blocks}
        while len(found) < plan.world.tolerances.motion_steps and abs(end - value) > 1e-9:
            h = min(step, abs(end - value))
            placed = _place(plan, value + sign * h, h, poses, at, rates, solver)
            if isinstance(placed, str) or (moved := corners.travel(poses, placed[0])) > 2 * SAMPLE_MM:
                step = h / 2
                if step < first * _FINEST:
                    out.halts.append(Halt(at=value, end=end))
                    break
                continue
            poses, at, rates = placed
            value += sign * h
            step = min(max(h * SAMPLE_MM / max(moved, 1e-9), h / 2), 2 * h)
            found.append((value, poses))
        ways.append(found)
    up = [w for w, end in zip(ways, plan.ends(), strict=True) if end > plan.rest]
    down = [w for w, end in zip(ways, plan.ends(), strict=True) if end < plan.rest]
    traced = [*reversed(down[0] if down else []), (plan.rest, still), *(up[0] if up else [])]
    steps = [corners.travel(a, b) for (_, a), (_, b) in zip(traced, traced[1:], strict=False)]
    return out.model_copy(update={
        "values": [v for v, _ in traced],
        "poses": [[np.round(p[c][:3], 9).reshape(-1).tolist() for c in plan.moving] for _, p in traced],
        "step_mm": max(steps, default=0.0),
    })  # fmt: skip


def _place(plan: Plan, value: float, h: float, before: dict[str, fr.Frame], at: dict[int, Position],
           rates: dict[int, np.ndarray], solver: Solver) -> tuple[dict[str, fr.Frame], dict[int, Position], dict[int, np.ndarray]] | str:  # fmt: skip
    """Every moving cluster's pose with the input at `value`, a step `h` on from `before`: by the stages in order,
    each block settled from its position before (`at`), started from its last step's motion (`rates`); or why not."""
    poses: dict[str, fr.Frame] = {}
    at, rates = dict(at), dict(rates)
    for stage in plan.stages:
        if stage.drive is not None:
            poses.update(dict.fromkeys(stage.clusters, stage.drive.at(value - plan.rest)))
        elif stage.host is not None:
            poses.update(dict.fromkeys(stage.clusters, poses[stage.host]))
        else:
            settled = _settle(stage, plan.rest, value, h, before, poses, at, rates, solver)
            if isinstance(settled, str):
                return settled
            poses.update(settled.poses)
    return poses, at, rates


def _settle(stage: Stage, rest: float, value: float, h: float, before: dict[str, fr.Frame], poses: dict[str, fr.Frame],
            at: dict[int, Position], rates: dict[int, np.ndarray], solver: Solver) -> Position | str:  # fmt: skip
    block = stage.block
    assert block is not None
    was = at[id(stage)]
    target = block.start.value + value - rest
    given0, given = ({g: p[g] for g in block.given} for p in (before, poses))
    x = solver.settle(block, rates[id(stage)] * h, block.parameters(target, was, given0, given))
    if x is None:
        return "it did not settle"
    found = block.check(block.moved(x, was), target, was, given)
    if isinstance(found, Position):
        at[id(stage)], rates[id(stage)] = found, x / h
    return found


class _Corners:
    """The moving clusters' leaves' box corners, as one array, each with its cluster."""

    def __init__(self, corners: dict[str, np.ndarray]) -> None:
        self.clusters = list(corners)
        self.points = np.vstack(list(corners.values()))
        self.of = np.repeat(np.arange(len(corners)), [len(p) for p in corners.values()])

    def travel(self, a: dict[str, fr.Frame], b: dict[str, fr.Frame]) -> float:
        """The furthest any of them moves from poses `a` to poses `b` (mm): each by its cluster's change of frame."""
        change = np.stack([b[c] - a[c] for c in self.clusters])[self.of]
        moved = np.einsum("nij,nj->ni", change[:, :3, :3], self.points) + change[:, :3, 3]
        return float(np.linalg.norm(moved, axis=1).max())


def _key(plan: Plan, solver: Solver, cache: ResultCache) -> str:
    """What a trace depends on, as plain values: never an object, whose text may carry a build's path."""

    def rounded(f: np.ndarray) -> list[object]:
        return np.round(f, 6).tolist()

    tol = plan.world.tolerances
    stages = []
    for s in plan.stages:
        block = s.block
        stages.append([s.clusters, s.drive.key() if s.drive else None, s.host, block and [
            block.clusters, block.given, block.driver, block.kind,
            [[m.label, m.mate.model_dump(mode="json"), m.a, m.b, rounded(m.fa), rounded(m.fb), sorted(m.holds)]
             for m in block.mates],
            [[r.label, r.relation.model_dump(mode="json"), r.a, r.b, rounded(r.fa), rounded(r.fb), r.law.key(), r.kinds,
              r.tolerance] for r in block.relations],
            {c: rounded(p) for c, p in block.corners.items()}]])  # fmt: skip
    return cache.key("trace", solver.name, solver.version, SAMPLE_MM, tol.position_mm, tol.angle_deg,
                     tol.motion_steps, plan.kind, plan.rest, plan.ends(), rounded(plan.link.a.frame), stages,
                     {c: rounded(plan.world.corners[c]) for c in plan.moving})  # fmt: skip
