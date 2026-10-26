"""Loop solvers: hitlcad's own, on scipy's least squares."""

from __future__ import annotations

from typing import Any

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_solvers() -> dict[str, Any]:
    from hitlcad.builtin.solvers.least_squares import LeastSquares

    return {LeastSquares.name: LeastSquares()}
