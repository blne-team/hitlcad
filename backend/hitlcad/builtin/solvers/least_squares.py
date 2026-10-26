"""hitlcad's own loop solver: each step guessed as the last one again, then settled by
`scipy.optimize.least_squares` on the problem's residuals; a step the problem does not take is halved, and where
halving gets nowhere the loop goes no further (it locks, or the solver loses it)."""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np

from hitlcad.core.loops import SAMPLE_MM, LoopProblem, Position

_FINEST = 1e-3  # of a first step: the smallest step tried before giving up


class LeastSquares:
    name = "least_squares"
    version = "1"

    def supports(self, problem: LoopProblem) -> str:
        return ""

    def trace(self, problem: LoopProblem, start: Position, end: float, budget: int) -> Iterator[Position]:
        from scipy.optimize import least_squares

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

                def off(x: np.ndarray, value: float = value, at: Position = at) -> np.ndarray:
                    return problem.residuals(problem.moved(x, at), value, at)

                guess = rate * h
                method = "lm" if len(off(guess)) >= len(guess) else "trf"
                fit = least_squares(off, guess, method=method, xtol=1e-12, ftol=1e-12, gtol=1e-12)
                found = problem.check(problem.moved(fit.x, at), value, at)
                if isinstance(found, Position):
                    break
                step = h / 2
                if step < first * _FINEST:
                    return
            moved = problem.travel(at.poses, found.poses)
            step = min(max(h * SAMPLE_MM / max(moved, 1e-9), h / 2), 2 * h)
            rate = fit.x / h
            at = found
            yield found
