"""hitlcad's loop solver: each step solved by CasADi's SQP method (its QP by qrqp) on the problem's compiled graph,
with the exact derivatives CasADi takes of it, started from the last step's motion scaled to this one; a step the
problem does not take is halved, and where halving gets nowhere the loop goes no further (it locks, or the solver
loses it)."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any
from weakref import WeakKeyDictionary

import numpy as np

from hitlcad.core.loops import SAMPLE_MM, LoopProblem, Position

_FINEST = 1e-3  # of a first step: the smallest step tried before giving up
_OPTIONS = {
    "qpsol": "qrqp",
    "qpsol_options": {"print_iter": False, "print_header": False, "print_info": False, "error_on_fail": False},
    "print_header": False, "print_iteration": False, "print_status": False, "print_time": False,
    "tol_pr": 1e-12, "tol_du": 1e-12, "max_iter": 50,
}  # fmt: skip


class Sqp:
    name = "sqp"
    version = "1"

    def __init__(self) -> None:
        self._solvers: WeakKeyDictionary[LoopProblem, Any] = WeakKeyDictionary()

    def supports(self, problem: LoopProblem) -> str:
        return ""

    def trace(self, problem: LoopProblem, start: Position, end: float, budget: int) -> Iterator[Position]:
        solve = self._solver(problem)
        sign = math.copysign(1.0, end - start.value)
        first = SAMPLE_MM if problem.kind == "travel" else math.degrees(SAMPLE_MM / problem.size)
        at, step, rate = start, first, np.zeros(6 * len(problem.clusters))
        for _ in range(budget):
            left = abs(end - at.value)
            if left <= 1e-9:
                return
            while True:
                h = min(step, left)
                value = at.value + sign * h
                x = np.asarray(solve(x0=rate * h, p=problem.parameters(value, at))["x"]).ravel()
                found = (
                    problem.check(problem.moved(x, at), value, at) if solve.stats()["success"] else "it did not settle"
                )
                if isinstance(found, Position):
                    break
                step = h / 2
                if step < first * _FINEST:
                    return
            moved = problem.travel(at.poses, found.poses)
            step = min(max(h * SAMPLE_MM / max(moved, 1e-9), h / 2), 2 * h)
            rate = x / h
            at = found
            yield found

    def _solver(self, problem: LoopProblem) -> Any:
        """The problem's SQP solver, made once."""
        import casadi as ca

        if problem not in self._solvers:
            self._solvers[problem] = ca.nlpsol("loop", "sqpmethod", problem.nlp(), _OPTIONS)
        return self._solvers[problem]
