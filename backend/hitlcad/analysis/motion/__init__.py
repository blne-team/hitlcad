"""Motion: every mate with a range of motion (its `limits`, or a full turn for a turn without them) is moved through
it, and nothing may collide on the way.

`plans` finds what moves. A mate's side and the gears it drives, each about a fixed axis, are swept exactly
(`tree`); a loop of mates, or a train a relation closes, is swept at the positions a loop solver gives (`loop`),
and the human watches it move. Here each mate gets its result; the rest of hitlcad gets the least gaps the sweeps
found (`least_gaps`, for clearance requirements) and the ranges a report carries (`reported`, for the panel).
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from typing import Any

import numpy as np

from hitlcad.analysis.base import Context, ResultCache, label, result
from hitlcad.analysis.model import AnalysisSettings, BuiltDesign
from hitlcad.analysis.motion import loop, tree
from hitlcad.analysis.motion.findings import Contact, Least
from hitlcad.analysis.motion.loop import REPORTED, LoopSwept, Solved
from hitlcad.analysis.motion.plans import NAME, Drive, LoopPlan, Plan, drives, plans, ranges, unit
from hitlcad.analysis.motion.tree import Swept
from hitlcad.core.report import Cause, MotionDrive, MotionPose, MotionRange, Result
from hitlcad.core.structure import Mate
from hitlcad.kernel.errors import (
    MOTION_CLEAR,
    MOTION_COLLISION,
    MOTION_HELD,
    MOTION_LOCKED,
    MOTION_LOOP,
    MOTION_PARTIAL,
    MOTION_SAMPLED,
    MOTION_WATCHED,
    ErrorKind,
)

__all__ = ["Motion", "least_gaps", "plans", "reported"]


class Motion:
    name = NAME
    stage = "motion"
    title = "Motion"
    summary = "Moving parts sweep through their range without colliding"

    def run(self, ctx: Context) -> list[Result]:
        out: list[Result] = []
        for p in ctx.built.motion_plans:
            if isinstance(p, Result):
                out.append(p)
            elif isinstance(p, LoopPlan):
                out += self._loop(p, loop.sweep(p, ctx.settings, ctx.cache), ctx.watched)
            else:
                out.append(self._verdict(p, tree.sweep(p, ctx.settings, ctx.cache)))
        return out

    def _loop(self, plan: LoopPlan, swept: LoopSwept, watched: Mapping[str, int]) -> list[Result]:
        """A result for each of the loop's mates with a range of motion, all from its one sweep."""
        out = []
        for i, link in enumerate(plan.links):
            mate: Mate = link.source
            kinds = [kind for kind, *_ in ranges(mate)]
            if not kinds:
                continue
            kind, status, detail, more, values = self._loop_verdict(plan, swept, i, kinds, watched.get(swept.key))
            out.append(result(self.name, kind, mate.address, status, detail,
                              [link.a.instance, link.b.instance, mate.a, mate.b, *more], values,
                              severity="info" if kind is MOTION_HELD else "error", cause="" if status else plan.cause))  # fmt: skip
        return out

    def _loop_verdict(self, plan: LoopPlan, swept: LoopSwept, i: int, kinds: list[str],
                      watched: int | None) -> tuple[ErrorKind, bool | None, str, list[str], dict[str, Any]]:  # fmt: skip
        """A mate's result from its loop's sweep; a loop clear at every position solved passes once the human has
        watched it move (on revision `watched`)."""
        link = plan.links[i]
        pair = f"{label(link.a)} ↔ {label(link.b)}"
        solved = swept.solved
        if solved is None:
            return MOTION_LOOP, None, f"{pair}: in a closed loop; {swept.why}", [], {}
        if solved.held:
            return MOTION_HELD, True, f"{pair}: held by the rest of its loop, so it does not move", [], {}
        if len(solved.values) < 2:
            why = "; ".join(s.why for s in solved.stops if s.why) or "it does not move from where it sits"
            return MOTION_LOOP, None, f"{pair}: in a closed loop the solver could not move: {why}", [], {}
        lead = plan.links[solved.driver]
        by = "" if i == solved.driver else f" with its loop, driven by {label(lead.a)} ↔ {label(lead.b)}"
        span = {k: solved.spans[i][k] for k in kinds}
        if past := self._past(plan, solved, i, span):
            return MOTION_LOCKED, False, f"{pair}{by}: {past}", [], {k: v[1] for k, v in span.items()}
        if swept.contacts:
            first = min(swept.contacts, key=lambda c: abs(c.at - solved.rest))
            return (MOTION_COLLISION, False, f"{pair}{by}: {self._loop_contacts(solved, swept.contacts)}",
                    [p for c in swept.contacts for p in (c.a, c.b)], {first.kind: first.at, "overlap_mm3": first.overlap_mm3})  # fmt: skip
        moves = ", ".join(self._moves(solved, i, k, *span[k]) for k in kinds)
        problems = [s.why for s in solved.stops if s.why]
        problems += [f"{a} against {b} is not measurable: {why}" for a, b, why in swept.unmeasurable]
        problems += [f"{a} and {b} already overlap by {mm3:.2f} mm³ where they sit (see interference), so their "
                     "motion is not checked" for a, b, mm3 in swept.resting]  # fmt: skip
        if problems:
            return (MOTION_PARTIAL, None, f"{pair} moves {moves}{by} without collision as far as it was checked; "
                    + "; ".join(problems), [], {})  # fmt: skip
        held = (f"; {solved.freedoms} more way{'s' if solved.freedoms > 1 else ''} it can move held where "
                f"{'they sit' if solved.freedoms > 1 else 'it sits'}" if solved.freedoms else "")  # fmt: skip
        clear = (f"{pair} moves {moves}{by}: clear at {len(solved.values)} solved positions, no point moving more than "
                 f"{solved.step_mm:.2f} mm between them{held}")  # fmt: skip
        values = {"positions": len(solved.values)}
        if watched is not None:
            return MOTION_WATCHED, True, f"{clear}; watched in the panel on revision {watched}", [], values
        return (
            MOTION_SAMPLED,
            None,
            f"{clear}; between them unchecked: the human watches it move in the panel to pass it",
            [],
            values,
        )

    @staticmethod
    def _moves(solved: Solved, i: int, kind: str, lo: float, hi: float) -> str:
        driving = i == solved.driver and kind == solved.kind
        if driving and solved.full_turn:
            return "a full turn"
        return f"{lo:.1f} … {hi:.1f}{unit(kind)}" + (" (as far as the loop goes)" if driving and solved.locks else "")

    @staticmethod
    def _past(plan: LoopPlan, solved: Solved, i: int, span: dict[str, tuple[float, float]]) -> str:
        """How a mate's motion in the loop runs against its own limits: the loop locks before the driver's, or
        takes another mate past its own."""
        limits, tol = plan.links[i].source.limits_of(), plan.world.tolerances
        for kind, (lo, hi) in span.items():
            bounds = getattr(limits, kind) if limits else None
            if not bounds:
                continue
            u, slack = unit(kind), tol.angle_deg if kind == "angle" else tol.position_mm
            if i == solved.driver and kind == solved.kind:
                if not solved.locks:
                    continue
                ends = list(bounds)
                for s in solved.locks:
                    up = s.end > solved.rest
                    ends[up] = (math.floor if up else math.ceil)(s.at * 10) / 10  # rounded towards where it sits
                at = " and ".join(f"{s.at:.1f}{u} on the way to {s.end:g}{u}" for s in solved.locks)
                return (f"the loop goes no further than {at}: limits.{kind} [{bounds[0]:g}, {bounds[1]:g}] run past "
                        f"it; make them [{ends[0]:g}, {ends[1]:g}], or change the links so that it goes further")  # fmt: skip
            if lo < bounds[0] - slack or hi > bounds[1] + slack:
                return (
                    f"the loop moves it {lo:.1f} … {hi:.1f}{u}, past its limits.{kind} [{bounds[0]:g}, {bounds[1]:g}]"
                )
        return ""

    @staticmethod
    def _loop_contacts(solved: Solved, contacts: list[Contact]) -> str:
        u = unit(solved.kind)
        hits = " and ".join(f"{c.a} hits {c.b} at {c.at:.1f}{u} on the way to {c.end:g}{u}"
                            for c in sorted(contacts, key=lambda c: c.at))  # fmt: skip
        return f"from {solved.rest:.1f}{u}, {hits}: clear {', '.join(sorted({c.b for c in contacts}))}"

    def _verdict(self, plan: Plan, swept: Swept) -> Result:
        link = plan.link
        mate: Mate = link.source
        about = [link.a.instance, link.b.instance, mate.a, mate.b]
        pair = f"{label(link.a)} ↔ {label(link.b)}"
        if swept.contacts:
            first = min(swept.contacts, key=lambda c: abs(c.at - plan.free.get(c.kind, 0.0)))
            causes = {Cause.contact(mate.address, c.a, c.b) for c in swept.contacts}
            return result(self.name, MOTION_COLLISION, mate.address, False, f"{pair}: {self._contacts(plan, swept.contacts)}",
                          [*about, *(p for c in swept.contacts for p in (c.a, c.b))],
                          {first.kind: first.at, "overlap_mm3": first.overlap_mm3},
                          cause=causes.pop() if len(causes) == 1 else "")  # fmt: skip
        ranges = ", ".join("a full turn" if continuous else f"{lo:g} … {hi:g}{unit(kind)}"
                           for kind, lo, hi, continuous in plan.ranges)  # fmt: skip
        problems = [f"{s.a} and {s.b} stay {s.gap_mm:.3f} mm apart from {s.at:.2f}{unit(s.kind)} on, too close to "
                    f"sweep further in {plan.world.tolerances.motion_steps} positions: give them clearance"
                    for s in swept.stopped]  # fmt: skip
        problems += [f"{a} against {b} is not measurable: {why}" for a, b, why in swept.unmeasurable]
        problems += [f"{a} and {b} already overlap by {mm3:.2f} mm³ where they sit (see interference), so their "
                     "motion is not checked" for a, b, mm3 in swept.resting]  # fmt: skip
        # Overlaps where they sit, and nothing else, are the interference analysis's problem.
        resting = {Cause.overlap(a, b) for a, b, _ in swept.resting}
        if problems:
            return result(self.name, MOTION_PARTIAL, mate.address, None, f"{pair} moves through {ranges} without collision as far as "
                          f"it was checked; " + "; ".join(problems),
                          [*about, *(p for s in swept.stopped for p in (s.a, s.b)),
                           *(p for a, b, *_ in [*swept.unmeasurable, *swept.resting] for p in (a, b))],
                          cause=resting.pop() if len(resting) == 1 and len(problems) == len(swept.resting) else "")  # fmt: skip
        checked = f"{swept.positions} positions checked" if swept.positions else "nothing in its reach meets its sweep"
        return result(self.name, MOTION_CLEAR, mate.address, True, f"{pair} moves through {ranges} without collision ({checked})",
                      about, {"positions": swept.positions})  # fmt: skip

    @staticmethod
    def _contacts(plan: Plan, contacts: list[Contact]) -> str:
        """Where it first hits something on the way from where it sits towards each end, and what to change."""
        out = []
        for kind, lo, hi, continuous in plan.ranges:
            found = [c for c in contacts if c.kind == kind]
            if not found:
                continue
            u, rest = unit(kind), plan.free.get(kind, 0.0)
            hits = " and ".join(f"{c.a} hits {c.b} at {c.at:.1f}{u} on the way to {c.end:g}{u}" for c in found)
            others = ", ".join(sorted({c.b for c in found}))
            if continuous:
                out.append(f"from {rest:.1f}{u}, {hits}: it cannot turn a full turn; clear {others}, or give the "
                           f"mate limits.{kind}")  # fmt: skip
                continue
            ends = [lo, hi]
            for c in found:
                up = c.end > rest
                ends[up] = (math.floor if up else math.ceil)(c.clear * 10) / 10  # rounded towards where it sits
            out.append(f"from {rest:.1f}{u}, {hits}: limits.{kind} [{lo:g}, {hi:g}] run past it; make them "
                       f"[{ends[0]:g}, {ends[1]:g}], or clear {others}")  # fmt: skip
        return "; ".join(out)


def least_gaps(built: BuiltDesign, settings: AnalysisSettings, cache: ResultCache) -> Iterator[tuple[str, Least]]:
    """Each least gap the sweeps found between pairs a clearance requirement names, with the mate whose motion
    brought them there (a loop's driver)."""
    for plan in built.motion_plans:
        if isinstance(plan, LoopPlan):
            swept: Swept | LoopSwept = loop.sweep(plan, settings, cache)
            if swept.solved is None:
                continue
            mate = plan.links[swept.solved.driver].source.address
        elif isinstance(plan, Plan):
            swept, mate = tree.sweep(plan, settings, cache), plan.link.source.address
        else:
            continue
        for least in swept.least:
            yield mate, least


def reported(built: BuiltDesign) -> list[MotionRange]:
    """The ranges of motion a report carries, as swept, for the panel to move: about fixed axes, or a loop's
    solved positions (at most `REPORTED`, evenly picked, so each one shown was checked)."""
    out = []
    for plan in built.motion_plans:
        if isinstance(plan, LoopPlan):
            solved = plan.swept.solved if plan.swept else None
            if solved is None or solved.held or len(solved.values) < 2:
                continue
            link = plan.links[solved.driver]
            n = len(solved.values)
            shown = sorted({solved.rest_index, *np.linspace(0, n - 1, min(n, REPORTED)).round().astype(int).tolist()})
            out.append(MotionRange(
                mate=link.source.address, pair=f"{label(link.a)} ↔ {label(link.b)}", kind=solved.kind,
                start=solved.values[0], end=solved.values[-1], continuous=solved.full_turn, current=solved.rest,
                values=[solved.values[i] for i in shown], key=plan.swept.key,
                poses=[MotionPose(instances=plan.leaves_of(cluster),
                                  frames=[np.round(solved.poses[i][c], 6).tolist() for i in shown])
                       for c, cluster in enumerate(solved.clusters)],
            ))  # fmt: skip
        elif isinstance(plan, Plan):
            for kind, start, end, continuous in plan.ranges:
                moved: dict[int, tuple[Drive, set[str]]] = {}  # one drive is shared by every cluster it moves
                for cluster, drive in drives(plan, kind)[0].items():
                    moved.setdefault(id(drive), (drive, set()))[1].add(cluster)
                out.append(MotionRange(
                    mate=plan.link.source.address, pair=f"{label(plan.link.a)} ↔ {label(plan.link.b)}", kind=kind,
                    start=start, end=end, continuous=continuous, current=plan.free.get(kind, 0.0),
                    drives=[MotionDrive(kind=d.axis.kind, origin=tuple(float(v) for v in d.axis.origin),
                                        axis=tuple(float(v) for v in d.axis.direction), rate=d.rate,
                                        instances=plan.world.leaves_of(clusters))
                            for d, clusters in moved.values()],
                ))  # fmt: skip
    return out
