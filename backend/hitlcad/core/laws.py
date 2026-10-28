"""Laws: how far one coordinate moves as another does, both counted from as assembled. A relation holds b = `at`(a)
(gears, a rack and a screw at a fixed ratio, a cam by its lift table); a drive moves a part along its axis by a law
of the input's motion. A sweep about fixed axes stays exact by bounding each step with a law's `steepest` slope."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Protocol

import numpy as np


class Law(Protocol):
    one_way: bool  # b follows a, and cannot drive it back (a cam's follower)

    def at(self, a: float) -> float:
        """b for `a`; 0 at 0."""

    def slope(self, a: float) -> float:
        """db/da at `a`."""

    def steepest(self, lo: float, hi: float) -> float:
        """The largest |slope| for a in [lo, hi]."""

    def bounds(self, lo: float, hi: float) -> tuple[float, float]:
        """The least and greatest b for a in [lo, hi]."""

    def key(self) -> list[object]:
        """What it is, as plain values: what a sweep that moves by it is cached by."""

    def symbolic(self, a: Any) -> Any:
        """`at` of a CasADi symbol: what a block solver compiles."""


@dataclass(frozen=True)
class Linear:
    """b = `ratio` × a."""

    ratio: float
    one_way: bool = False

    def at(self, a: float) -> float:
        return self.ratio * a

    def slope(self, a: float) -> float:
        return self.ratio

    def steepest(self, lo: float, hi: float) -> float:
        return abs(self.ratio)

    def bounds(self, lo: float, hi: float) -> tuple[float, float]:
        ends = (self.ratio * lo, self.ratio * hi)
        return min(ends), max(ends)

    def key(self) -> list[object]:
        return ["linear", round(self.ratio, 12)]

    def symbolic(self, a: Any) -> Any:
        return self.ratio * a


@dataclass(frozen=True)
class Table:
    """b by a table of (a, b) points, ascending in a, through a smooth curve that does not overshoot them (PCHIP),
    from `start`: b = f(start + a) − f(start). A table that repeats every `period` (a cam's turn) gives one period;
    a point at its end, the same as its first, may be left out."""

    points: tuple[tuple[float, float], ...]
    start: float = 0.0
    period: float | None = None
    one_way: bool = True

    @cached_property
    def _xy(self) -> tuple[np.ndarray, np.ndarray]:
        """The points of one period, its end left out."""
        xs, ys = np.array(self.points, dtype=float).T
        if self.period is not None and math.isclose(xs[-1], xs[0] + self.period):
            xs, ys = xs[:-1], ys[:-1]
        return xs, ys

    @cached_property
    def _f(self):  # type: ignore[no-untyped-def]
        from scipy.interpolate import PchipInterpolator

        xs, ys = self._xy
        if self.period is not None:  # two points either side from the next periods, so the curve runs on smoothly
            p = self.period
            xs, ys = np.r_[xs[-2:] - p, xs, xs[:2] + p], np.r_[ys[-2:], ys, ys[:2]]
        return PchipInterpolator(xs, ys, extrapolate=False)

    @cached_property
    def _df(self):  # type: ignore[no-untyped-def]
        return self._f.derivative()

    def _x(self, a: float) -> float:
        x = self.start + a
        if self.period is None:
            return x
        x0 = float(self._xy[0][0])
        return x0 + (x - x0) % self.period

    def _over(self, xs: list[float], lo: float, hi: float) -> list[float]:
        """Each a in [lo, hi] where the table is at one of `xs` (of its one period, when it repeats)."""
        if self.period is None:
            return [x - self.start for x in xs if lo <= x - self.start <= hi]
        p, out = self.period, []
        for x in xs:
            k = math.ceil((self.start + lo - x) / p)
            while (a := x + k * p - self.start) <= hi:
                out.append(a)
                k += 1
        return out

    def _turns(self, df) -> list[float]:  # type: ignore[no-untyped-def]
        """Where `df` is 0, within the table's one period."""
        xs = self._xy[0]
        last = xs[0] + self.period if self.period is not None else xs[-1]
        return [float(r) for r in df.roots(extrapolate=False) if xs[0] <= r < last]

    def of(self, x: float) -> float:
        """The table's own b at its a = `x`."""
        return float(self._f(self._x(x - self.start)))

    def at(self, a: float) -> float:
        return self.of(self.start + a) - self.of(self.start)

    def slope(self, a: float) -> float:
        return float(self._df(self._x(a)))

    def steepest(self, lo: float, hi: float) -> float:
        """Exact: on each piece the slope is a quadratic, steepest at a piece's ends or where it turns."""
        points = [lo, hi, *self._over([*self._xy[0], *self._turns(self._df.derivative())], lo, hi)]
        return max(abs(self.slope(a)) for a in points)

    def bounds(self, lo: float, hi: float) -> tuple[float, float]:
        """Exact: b is greatest or least at an end, or where its slope is 0."""
        values = [self.at(a) for a in (lo, hi, *self._over(self._turns(self._df), lo, hi))]
        return min(values), max(values)

    def key(self) -> list[object]:
        return ["table", [[round(x, 9), round(y, 9)] for x, y in self.points], round(self.start, 9), self.period]

    def symbolic(self, a: Any) -> Any:
        """The same curve, piece by piece: each piece's cubic, chosen by where a falls."""
        import casadi as ca

        x = self.start + a
        if self.period is not None:
            x0 = float(self._xy[0][0])
            x = x - self.period * ca.floor((x - x0) / self.period)
        breaks, c = self._f.x, self._f.c
        inner = ca.DM(breaks[1:-1])
        dx = x - ca.pw_const(x, inner, ca.DM(breaks[:-1]))
        y = sum(ca.pw_const(x, inner, ca.DM(c[k])) * dx ** (3 - k) for k in range(4))
        return y - self.of(self.start)


@dataclass(frozen=True)
class Composed:
    """b = `scale_out` × outer(`scale_in` × inner(a)): a law carried on through a relation."""

    outer: Law
    inner: Law
    scale_in: float = 1.0
    scale_out: float = 1.0

    @property
    def one_way(self) -> bool:
        return self.outer.one_way or self.inner.one_way

    def at(self, a: float) -> float:
        return self.scale_out * self.outer.at(self.scale_in * self.inner.at(a))

    def slope(self, a: float) -> float:
        return self.scale_out * self.outer.slope(self.scale_in * self.inner.at(a)) * self.scale_in * self.inner.slope(a)

    def steepest(self, lo: float, hi: float) -> float:
        between = sorted(self.scale_in * b for b in self.inner.bounds(lo, hi))
        return abs(self.scale_out * self.scale_in) * self.outer.steepest(*between) * self.inner.steepest(lo, hi)

    def bounds(self, lo: float, hi: float) -> tuple[float, float]:
        between = sorted(self.scale_in * b for b in self.inner.bounds(lo, hi))
        ends = sorted(self.scale_out * b for b in self.outer.bounds(*between))
        return ends[0], ends[1]

    def key(self) -> list[object]:
        return ["composed", self.outer.key(), self.inner.key(), round(self.scale_in, 12), round(self.scale_out, 12)]

    def symbolic(self, a: Any) -> Any:
        return self.scale_out * self.outer.symbolic(self.scale_in * self.inner.symbolic(a))


def compose(outer: Law, inner: Law, scale_in: float = 1.0, scale_out: float = 1.0) -> Law:
    """outer after inner, scaled: a straight line where both are one."""
    if isinstance(outer, Linear) and isinstance(inner, Linear):
        return Linear(scale_out * outer.ratio * scale_in * inner.ratio)
    return Composed(outer, inner, scale_in, scale_out)
