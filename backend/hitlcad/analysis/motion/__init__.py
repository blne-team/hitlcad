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
from hitlcad.analysis.motion.findings import Contact, Least, Parted
from hitlcad.analysis.motion.loop import REPORTED, LoopSwept, Solved
from hitlcad.analysis.motion.plans import NAME, Drive, Driven, LoopPlan, Plan, drives, plans, ranges, unit
from hitlcad.analysis.motion.tree import Swept
from hitlcad.core import frames as fr
from hitlcad.core.laws import Linear
from hitlcad.core.report import Cause, MotionDrive, MotionPose, MotionRange, Result
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
    ErrorKind,
)

__all__ = ["Motion", "least_gaps", "plans", "positions", "reported"]


class Motion:
    name = NAME
    stage = "motion"
    title = "Motion"
    summary = "Moving parts sweep through their range without colliding"

    def run(self, ctx: Context) -> list[Result]:
        out: list[Result] = []
        inputs = [p for p in ctx.built.motion_plans if isinstance(p, Plan)]
        for p in ctx.built.motion_plans:
            if isinstance(p, Result):
                out.append(p)
            elif isinstance(p, LoopPlan):
                out += self._loop(p, loop.sweep(p, ctx.settings, ctx.cache), ctx.watched)
            else:
                out.append(self._verdict(p, tree.sweep(p, ctx.settings, ctx.cache), inputs))
                out += [self._driven(p, d) for d in p.driven]
        return out

    @staticmethod
    def _driven(plan: Plan, d: Driven) -> Result:
        """A mate an input drives: how far it moves through the input's range, which must lie within its limits."""
        mate: Mate = d.link.source
        about = [d.link.a.instance, d.link.b.instance, mate.a, mate.b]
        pair, by = f"{label(d.link.a)} ↔ {label(d.link.b)}", f"{label(plan.link.a)} ↔ {label(plan.link.b)}"
        (lo, hi), u = d.span, unit(d.kind)
        limits, tol = mate.limits_of(), plan.world.tolerances
        bounds = getattr(limits, d.kind) if limits else None
        slack = tol.angle_deg if d.kind == "angle" else tol.position_mm
        if bounds and (lo < bounds[0] - slack or hi > bounds[1] + slack):
            return result(NAME, MOTION_LOCKED, mate.address, False, f"{pair}: driven by {by} through its range, it "
                          f"moves {lo:.1f} … {hi:.1f}{u}, past its limits.{d.kind} [{bounds[0]:g}, {bounds[1]:g}]: "
                          f"narrow the range of {by}, or widen these", about,
                          {d.kind: lo if lo < bounds[0] - slack else hi})  # fmt: skip
        return result(NAME, MOTION_DRIVEN, mate.address, True, f"{pair} moves {lo:.1f} … {hi:.1f}{u}, driven by {by}: "
                      "checked along its sweep", about, severity="info")  # fmt: skip

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
        if swept.parted:
            return (MOTION_PARTED, False, f"{pair}{by}: {_parted(swept.parted, solved.kind)}",
                    [p for c in swept.parted for p in (c.a, c.b)], {swept.parted[0].kind: swept.parted[0].at})  # fmt: skip
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

    def _verdict(self, plan: Plan, swept: Swept, inputs: list[Plan]) -> Result:
        link = plan.link
        mate: Mate = link.source
        about = [link.a.instance, link.b.instance, mate.a, mate.b]
        pair = f"{label(link.a)} ↔ {label(link.b)}"
        if swept.parted:
            return result(self.name, MOTION_PARTED, mate.address, False, f"{pair}: {_parted(swept.parted, swept.parted[0].kind)}",
                          [*about, *(p for c in swept.parted for p in (c.a, c.b))],
                          {swept.parted[0].kind: swept.parted[0].at})  # fmt: skip
        if swept.contacts:
            first = min(swept.contacts, key=lambda c: abs(c.at - plan.free.get(c.kind, 0.0)))
            causes = {Cause.contact(mate.address, c.a, c.b) for c in swept.contacts}
            return result(self.name, MOTION_COLLISION, mate.address, False,
                          f"{pair}: {self._contacts(plan, swept.contacts)}{self._held(plan, swept.contacts, inputs)}",
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
    def _held(plan: Plan, contacts: list[Contact], inputs: list[Plan]) -> str:
        """What the sweep hit that an input of its own moves, held where it sits meanwhile: a relation may be missing
        (a cam turning against its follower)."""
        find, out = plan.world.clusters.find, []
        for c in contacts:
            cluster = find(c.b)
            if cluster in drives(plan, c.kind)[0]:
                continue
            own = min(
                (p for p in inputs if p is not plan and cluster in p.moving), key=lambda p: len(p.moving), default=None
            )
            if own is not None:
                line = (
                    f"; {c.b} stays where it sits on {label(own.link.a)} ↔ {label(own.link.b)}, a motion of its "
                    "own: if this motion moves it (a cam its follower), declare the relation that couples them"
                )
                if line not in out:
                    out.append(line)
        return "".join(out)

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


def _parted(parted: list[Parted], kind: str) -> str:
    u = unit(kind)
    return " and ".join(
        f"{c.a} stands {c.gap_mm:.3f} mm off {c.b} at {c.at:.1f}{u} on the way to {c.end:g}{u}, where "
        f"{c.relation} keeps them in contact: the profile and its law disagree there"
        for c in parted
    )


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


def positions(
    built: BuiltDesign, settings: AnalysisSettings, cache: ResultCache
) -> Iterator[tuple[str, dict[str, fr.Frame]]]:
    """Every position the motion moves through, as the mate that moves it (a loop's driver) and the motion of each
    leaf it moves from where it sits: an input's range a degree (or half a millimetre) apart, by its drives' laws,
    and a loop's positions solved."""
    for plan in built.motion_plans:
        if isinstance(plan, LoopPlan):
            solved = loop.sweep(plan, settings, cache).solved
            if solved is None:
                continue
            mate, carried = plan.links[solved.driver].source.address, {c: plan.leaves_of(c) for c in solved.clusters}
            stacked = solved.stacked()
            for i in range(len(solved.values)):
                yield mate, {leaf: stacked[c][i] for c in solved.clusters for leaf in carried[c]}
        elif isinstance(plan, Plan):
            for kind, lo, hi, continuous in plan.ranges:
                rest, driven = plan.free.get(kind, 0.0), drives(plan, kind)[0]
                carried = {c: plan.world.leaves_of({c}) for c in driven}
                start, end = (-180.0, 180.0) if continuous else (lo - rest, hi - rest)
                n = max(math.ceil((end - start) / (1.0 if kind == "angle" else 0.5)), 1)
                for value in np.linspace(start, end, n + 1).tolist():
                    yield (
                        plan.link.source.address,
                        {leaf: d.at(value) for c, d in driven.items() for leaf in carried[c]},
                    )


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
                current = plan.free.get(kind, 0.0)
                lo, hi = (0.0, 360.0) if continuous else (start - current, end - current)  # the panel's slider
                out.append(MotionRange(
                    mate=plan.link.source.address, pair=f"{label(plan.link.a)} ↔ {label(plan.link.b)}", kind=kind,
                    start=start, end=end, continuous=continuous, current=current,
                    drives=[MotionDrive(kind=d.axis.kind, origin=tuple(float(v) for v in d.axis.origin),
                                        axis=tuple(float(v) for v in d.axis.direction), law=_samples(d, kind, lo, hi),
                                        instances=plan.world.leaves_of(clusters))
                            for d, clusters in moved.values()],
                ))  # fmt: skip
    return out


def _samples(drive: Drive, kind: str, lo: float, hi: float) -> list[tuple[float, float]]:
    """The drive's law from `lo` to `hi` of the mate's motion, as points the panel moves between: its ends for a
    straight line, else a degree (or half a millimetre) apart."""
    n = 1 if isinstance(drive.law, Linear) else max(math.ceil((hi - lo) / (1.0 if kind == "angle" else 0.5)), 1)
    return [(x, drive.law.at(x)) for x in np.linspace(lo, hi, n + 1).tolist()]
