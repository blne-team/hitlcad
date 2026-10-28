"""Motion: every input (a mate's free motion with a range, its `limits` or a full turn for a turn without them, that
nothing before it already moves) is moved through its range, with everything it moves, and nothing may collide on
the way.

`plans` finds the inputs and what each moves, stage by stage; `path` gives an input's positions (straight from its
laws, or traced through its blocks); `sweep` checks them for collisions, exactly where every stage moves by a law,
else at the positions traced, which the human watches. Here each input gets its result, and each motion it drives
one; the rest of hitlcad gets the least gaps the sweeps found (`least_gaps`, for clearance requirements), the
positions (`motions`, for span requirements) and the ranges a report carries (`reported`, for the panel).
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from typing import Any

import numpy as np

from hitlcad.analysis.base import Context, ResultCache, label, result
from hitlcad.analysis.model import AnalysisSettings, BuiltDesign
from hitlcad.analysis.motion import path
from hitlcad.analysis.motion.findings import Contact, Least, Parted
from hitlcad.analysis.motion.path import Traced
from hitlcad.analysis.motion.plans import NAME, Drive, FreeMotion, Plan, plans, unit
from hitlcad.analysis.motion.sweep import Swept, sweep
from hitlcad.core import frames as fr
from hitlcad.core.laws import Linear
from hitlcad.core.report import Cause, MotionDrive, MotionPose, MotionRange, MotionStretch, Result
from hitlcad.core.structure import Mate
from hitlcad.kernel.errors import (
    MOTION_CLEAR,
    MOTION_COLLISION,
    MOTION_DRIVEN,
    MOTION_HELD,
    MOTION_LOCKED,
    MOTION_LOOP,
    MOTION_PARTED,
    MOTION_PARTIAL,
    MOTION_SAMPLED,
    MOTION_WATCHED,
)

__all__ = ["Motion", "least_gaps", "motions", "plans", "reported"]

REPORTED = 400  # the most positions of a traced motion a report carries, for the panel


class Motion:
    name = NAME
    stage = "motion"
    title = "Motion"
    summary = "Moving parts sweep through their range without colliding"

    def run(self, ctx: Context) -> list[Result]:
        out: list[Result] = []
        found = ctx.built.motion_plans
        inputs = [p for p in found if isinstance(p, Plan)]
        for p in found:
            if isinstance(p, Result):
                out.append(p)
                continue
            swept = sweep(p, ctx.settings, ctx.cache)
            out.append(_verdict(p, swept, inputs, ctx.watched))
            traced = swept.traced if swept.traced is not None else path.positions(p, ctx.settings, ctx.cache)
            if len(traced.values) > 1:
                out += [_driven(p, m, traced) for m in p.driven]
        return out


def _pair(plan: Plan) -> str:
    return f"{label(plan.link.a)} ↔ {label(plan.link.b)}"


def _about(plan: Plan) -> list[str]:
    mate: Mate = plan.link.source
    return [plan.link.a.instance, plan.link.b.instance, mate.a, mate.b]


def _verdict(plan: Plan, swept: Swept, inputs: list[Plan], watched: Mapping[str, int]) -> Result:
    mate: Mate = plan.link.source
    pair, about, u = _pair(plan), _about(plan), unit(plan.kind)
    traced = swept.traced
    if traced is not None and traced.why:
        return result(NAME, MOTION_LOOP, mate.address, None, f"{pair}: {traced.why}", about)
    if traced is not None and traced.held:
        return result(NAME, MOTION_HELD, mate.address, True, f"{pair}: held by the rest of its mechanism, so it does "
                      "not move", about, severity="info")  # fmt: skip
    if traced is not None and len(traced.values) < 2:
        return result(NAME, MOTION_LOOP, mate.address, None, f"{pair}: its solver could not move it from where it sits",
                      about)  # fmt: skip
    if swept.parted:
        first = swept.parted[0]
        return result(NAME, MOTION_PARTED, mate.address, False, f"{pair}: {_parted(swept.parted, plan.kind)}",
                      [*about, *(p for c in swept.parted for p in (c.a, c.b))], {first.kind: first.at})  # fmt: skip
    if swept.contacts:
        first = min(swept.contacts, key=lambda c: abs(c.at - plan.rest))
        causes = {Cause.contact(mate.address, c.a, c.b) for c in swept.contacts}
        return result(NAME, MOTION_COLLISION, mate.address, False,
                      f"{pair}: {_contacts(plan, swept.contacts)}{_held(plan, swept.contacts, inputs)}",
                      [*about, *(p for c in swept.contacts for p in (c.a, c.b))],
                      {first.kind: first.at, "overlap_mm3": first.overlap_mm3},
                      cause=causes.pop() if len(causes) == 1 else "")  # fmt: skip
    if traced is not None and traced.locks and not plan.continuous:
        return result(NAME, MOTION_LOCKED, mate.address, False, f"{pair}: {_locked(plan, traced)}", about,
                      {plan.kind: traced.locks[0].at})  # fmt: skip
    problems = [f"{s.a} and {s.b} stay {s.gap_mm:.3f} mm apart from {s.at:.2f}{u} on, too close to sweep further in "
                f"{plan.world.tolerances.motion_steps} positions: give them clearance" for s in swept.stopped]  # fmt: skip
    problems += [f"{a} against {b} is not measurable: {why}" for a, b, why in swept.unmeasurable]
    problems += [f"{a} and {b} already overlap by {mm3:.2f} mm³ where they sit (see interference), so their "
                 "motion is not checked" for a, b, mm3 in swept.resting]  # fmt: skip
    moves = _moves(plan, traced)
    if problems:
        resting = {Cause.overlap(a, b) for a, b, _ in swept.resting}
        return result(NAME, MOTION_PARTIAL, mate.address, None, f"{pair} moves {moves} without collision as far as it "
                      f"was checked; " + "; ".join(problems),
                      [*about, *(p for s in swept.stopped for p in (s.a, s.b)),
                       *(p for a, b, *_ in [*swept.unmeasurable, *swept.resting] for p in (a, b))],
                      cause=resting.pop() if len(resting) == 1 and len(problems) == len(swept.resting) else "")  # fmt: skip
    if traced is None:
        checked = f"{swept.positions} positions checked" if swept.positions else "nothing in its reach meets its sweep"
        return result(NAME, MOTION_CLEAR, mate.address, True, f"{pair} moves through {moves} without collision "
                      f"({checked})", about, {"positions": swept.positions})  # fmt: skip
    held = (f"; {traced.freedoms} more way{'s' if traced.freedoms > 1 else ''} it can move held where "
            f"{'they sit' if traced.freedoms > 1 else 'it sits'}" if traced.freedoms else "")  # fmt: skip
    clear = (f"{pair} moves {moves}: clear at {len(traced.values)} solved positions, no point moving more than "
             f"{traced.step_mm:.2f} mm between them{held}")  # fmt: skip
    values = {"positions": len(traced.values)}
    if (on := watched.get(swept.key)) is not None:
        return result(NAME, MOTION_WATCHED, mate.address, True, f"{clear}; watched in the panel on revision {on}", about,
                      values)  # fmt: skip
    return result(NAME, MOTION_SAMPLED, mate.address, None, f"{clear}; between them unchecked: the human watches it "
                  "move in the panel to pass it", about, values)  # fmt: skip


def _moves(plan: Plan, traced: Traced | None) -> str:
    u = unit(plan.kind)
    if traced is None:
        return "a full turn" if plan.continuous else f"{plan.start:g} … {plan.end:g}{u}"
    if plan.continuous and not traced.halts:
        return "a full turn"
    far = " (as far as it goes)" if traced.locks else ""
    return f"{traced.values[0]:.1f} … {traced.values[-1]:.1f}{u}{far}"


def _locked(plan: Plan, traced: Traced) -> str:
    """Where the motion goes no further than its limits, and the limits to make them."""
    u, ends = unit(plan.kind), [plan.start, plan.end]
    for h in traced.locks:
        up = h.end > plan.rest
        ends[up] = (math.floor if up else math.ceil)(h.at * 10) / 10  # rounded towards where it sits
    at = " and ".join(f"{h.at:.1f}{u} on the way to {h.end:g}{u}" for h in traced.locks)
    return (f"it goes no further than {at}: limits.{plan.kind} [{plan.start:g}, {plan.end:g}] run past it; make them "
            f"[{ends[0]:g}, {ends[1]:g}], or change the links so that it goes further")  # fmt: skip


def _contacts(plan: Plan, contacts: list[Contact]) -> str:
    """Where it first hits something on the way from where it sits towards each end, and what to change."""
    u, kind = unit(plan.kind), plan.kind
    hits = " and ".join(f"{c.a} hits {c.b} at {c.at:.1f}{u} on the way to {c.end:g}{u}" for c in contacts)
    others = ", ".join(sorted({c.b for c in contacts}))
    if plan.continuous:
        return (f"from {plan.rest:.1f}{u}, {hits}: it cannot turn a full turn; clear {others}, or give the mate "
                f"limits.{kind}")  # fmt: skip
    ends = [plan.start, plan.end]
    for c in contacts:
        up = c.end > plan.rest
        ends[up] = (math.floor if up else math.ceil)(c.clear * 10) / 10  # rounded towards where it sits
    return (f"from {plan.rest:.1f}{u}, {hits}: limits.{kind} [{plan.start:g}, {plan.end:g}] run past it; make them "
            f"[{ends[0]:g}, {ends[1]:g}], or clear {others}")  # fmt: skip


def _held(plan: Plan, contacts: list[Contact], inputs: list[Plan]) -> str:
    """What the sweep hit that an input of its own moves, held where it sits meanwhile: a relation may be missing (a
    cam turning against its follower)."""
    find, out = plan.world.clusters.find, []
    for c in contacts:
        cluster = find(c.b)
        if cluster in plan.moving:
            continue
        own = min(
            (p for p in inputs if p is not plan and cluster in p.moving), key=lambda p: len(p.moving), default=None
        )
        if own is not None:
            line = (f"; {c.b} stays where it sits on {_pair(own)}, a motion of its own: if this motion moves it (a cam "
                    "its follower), declare the relation that couples them")  # fmt: skip
            if line not in out:
                out.append(line)
    return "".join(out)


def _parted(parted: list[Parted], kind: str) -> str:
    u = unit(kind)
    return " and ".join(
        f"{c.a} stands {c.gap_mm:.3f} mm off {c.b} at {c.at:.1f}{u} on the way to {c.end:g}{u}, where "
        f"{c.relation} keeps them in contact: the profile and its law disagree there"
        for c in parted
    )


def _driven(plan: Plan, m: FreeMotion, traced: Traced) -> Result:
    """A motion an input drives: how far it moves through the input's positions, which must lie within its limits."""
    mate: Mate = m.link.source
    about = [m.link.a.instance, m.link.b.instance, mate.a, mate.b]
    pair, by = f"{label(m.link.a)} ↔ {label(m.link.b)}", _pair(plan)
    lo, hi = _span(plan, m, traced)
    limits, tol, u = mate.limits_of(), plan.world.tolerances, unit(m.kind)
    bounds = getattr(limits, m.kind) if limits else None
    slack = tol.angle_deg if m.kind == "angle" else tol.position_mm
    if bounds and (lo < bounds[0] - slack or hi > bounds[1] + slack):
        return result(NAME, MOTION_LOCKED, mate.address, False, f"{pair}: driven by {by} through its range, it moves "
                      f"{lo:.1f} … {hi:.1f}{u}, past its limits.{m.kind} [{bounds[0]:g}, {bounds[1]:g}]: narrow the range "
                      f"of {by}, or widen these", about, {m.kind: lo if lo < bounds[0] - slack else hi})  # fmt: skip
    return result(NAME, MOTION_DRIVEN, mate.address, True, f"{pair} moves {lo:.1f} … {hi:.1f}{u}, driven by {by}: "
                  "checked along its sweep", about, severity="info")  # fmt: skip


def _span(plan: Plan, m: FreeMotion, traced: Traced) -> tuple[float, float]:
    """The least and most of a mate's free motion through the positions, counted on past a half turn."""
    find, poses, n = plan.world.clusters.find, traced.stacked(), len(traced.values)
    a, b = (poses.get(find(e.instance), np.broadcast_to(fr.IDENTITY, (n, 4, 4))) for e in (m.link.a, m.link.b))
    mate: Mate = m.link.source
    seen = fr.rigid_inverse(a @ m.link.a.frame) @ (b @ mate.facing(m.link.b.frame))
    values = np.degrees(np.unwrap(np.arctan2(seen[:, 1, 0], seen[:, 0, 0]))) if m.kind == "angle" else seen[:, 2, 3]
    rest = values[traced.rest_index]
    sits = mate.verify(m.link.a.frame, mate.facing(m.link.b.frame), "axial", plan.world.tolerances).free[m.kind]
    return float(values.min() - rest + sits), float(values.max() - rest + sits)


def least_gaps(built: BuiltDesign, settings: AnalysisSettings, cache: ResultCache) -> Iterator[tuple[str, Least]]:
    """Each least gap the sweeps found between pairs a clearance requirement names, with the input that brought them
    there."""
    for plan in built.motion_plans:
        if isinstance(plan, Plan):
            for least in sweep(plan, settings, cache).least:
                yield plan.link.source.address, least


def motions(built: BuiltDesign, settings: AnalysisSettings, cache: ResultCache,
            instances: set[str]) -> Iterator[tuple[str, dict[str, np.ndarray]]]:  # fmt: skip
    """For each input that moves any of `instances` (leaves), the input and each of those it moves, its motion from
    where it sits at every position (a stack of frames): exact ones a degree (or half a millimetre) apart, traced
    ones as traced."""
    for plan in built.motion_plans:
        if not isinstance(plan, Plan):
            continue
        find = plan.world.clusters.find
        moving = {leaf: find(leaf) for leaf in instances if find(leaf) in plan.moving}
        if not moving:
            continue
        traced = path.positions(plan, settings, cache)
        if len(traced.values) > 1:
            stacked = traced.stacked()
            yield plan.link.source.address, {leaf: stacked[c] for leaf, c in moving.items()}


def _stretches(plan: Plan, traced: Traced) -> list[MotionStretch]:
    """What deforms as the plan moves (springs whose ends it carries apart or together), at its positions (at most
    `REPORTED` of them, evenly picked)."""
    find, n = plan.world.clusters.find, len(traced.values)
    if n < 2:
        return []
    stacked, still = traced.stacked(), np.broadcast_to(fr.IDENTITY, (n, 4, 4))

    def carried(leaf: str | None) -> np.ndarray:
        return still if leaf is None else stacked.get(find(leaf), still)

    shown = np.linspace(0, n - 1, min(n, REPORTED)).round().astype(int).tolist()
    out = []
    for leaf, s in plan.world.stretched.items():
        seat, top = carried(s.on_seat), carried(s.on_top)
        if seat is still and top is still:
            continue
        frames = [np.round(s.at(seat[i], top[i])[:3], 6).reshape(-1).tolist() for i in shown]
        out.append(MotionStretch(instances=[leaf], values=[traced.values[i] for i in shown], frames=frames))
    return out


def reported(built: BuiltDesign, settings: AnalysisSettings, cache: ResultCache) -> list[MotionRange]:
    """The ranges of motion a report carries, as swept, for the panel to move: about fixed axes by samples of their
    laws, or a traced motion's positions (at most `REPORTED`, evenly picked, so each one shown was checked)."""
    out = []
    for plan in built.motion_plans:
        if not isinstance(plan, Plan) or plan.swept is None:
            continue
        common: dict[str, Any] = {"mate": plan.link.source.address, "pair": _pair(plan), "kind": plan.kind,
                                  "current": plan.rest,
                                  "stretches": _stretches(plan, path.positions(plan, settings, cache))}  # fmt: skip
        traced = plan.swept.traced
        if traced is None:
            moved: dict[int, tuple[Drive, set[str]]] = {}  # one drive is shared by every cluster it moves
            for s in plan.stages:
                if s.drive is not None:
                    moved.setdefault(id(s.drive), (s.drive, set()))[1].update(s.clusters)
            lo, hi = (0.0, 360.0) if plan.continuous else (plan.start - plan.rest, plan.end - plan.rest)
            out.append(MotionRange(**common, start=plan.start, end=plan.end, continuous=plan.continuous, drives=[
                MotionDrive(kind=d.axis.kind, origin=tuple(float(v) for v in d.axis.origin),
                            axis=tuple(float(v) for v in d.axis.direction), law=_samples(d, plan.kind, lo, hi),
                            instances=plan.world.leaves_of(clusters))
                for d, clusters in moved.values()]))  # fmt: skip
            continue
        if traced.held or len(traced.values) < 2:
            continue
        n, stacked = len(traced.values), traced.stacked()
        shown = sorted({traced.rest_index, *np.linspace(0, n - 1, min(n, REPORTED)).round().astype(int).tolist()})
        out.append(MotionRange(**common, start=traced.values[0], end=traced.values[-1],
                               continuous=plan.continuous and not traced.halts,
                               values=[traced.values[i] for i in shown], key=plan.swept.key,
                               poses=[MotionPose(instances=plan.world.leaves_of({c}),
                                                 frames=[np.round(stacked[c][i][:3], 6).reshape(-1).tolist() for i in shown])
                                      for c in traced.clusters]))  # fmt: skip
    return out


def _samples(drive: Drive, kind: str, lo: float, hi: float) -> list[tuple[float, float]]:
    """The drive's law from `lo` to `hi` of the input, as points the panel moves between: its ends for a straight
    line, else a degree (or half a millimetre) apart."""
    n = 1 if isinstance(drive.law, Linear) else max(math.ceil((hi - lo) / (1.0 if kind == "angle" else 0.5)), 1)
    return [(x, drive.law.at(x)) for x in np.linspace(lo, hi, n + 1).tolist()]
