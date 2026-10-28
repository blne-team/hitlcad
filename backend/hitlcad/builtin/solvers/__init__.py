"""Block solvers: hitlcad's own, CasADi's SQP method on the block's compiled graph."""

from __future__ import annotations

from typing import Any

from hitlcad.kernel.plugins import hookimpl


@hookimpl
def hitlcad_solvers() -> dict[str, Any]:
    from hitlcad.builtin.solvers.sqp import Sqp

    return {Sqp.name: Sqp()}
