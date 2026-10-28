"""hitlcad's block solver: CasADi's SQP method (its QP by qrqp) on the block's compiled graph, with the exact
derivatives CasADi takes of it."""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

import numpy as np

from hitlcad.core.loops import Block

_OPTIONS = {
    "qpsol": "qrqp",
    "qpsol_options": {"print_iter": False, "print_header": False, "print_info": False, "error_on_fail": False},
    "print_header": False, "print_iteration": False, "print_status": False, "print_time": False,
    "tol_pr": 1e-12, "tol_du": 1e-12, "max_iter": 50,
}  # fmt: skip


class Sqp:
    name = "sqp"
    version = "2"

    def __init__(self) -> None:
        self._solvers: WeakKeyDictionary[Block, Any] = WeakKeyDictionary()

    def supports(self, block: Block) -> str:
        return ""

    def settle(self, block: Block, guess: np.ndarray, parameters: np.ndarray) -> np.ndarray | None:
        import casadi as ca

        if block not in self._solvers:
            self._solvers[block] = ca.nlpsol("block", "sqpmethod", block.nlp(), _OPTIONS)
        solve = self._solvers[block]
        x = np.asarray(solve(x0=guess, p=parameters)["x"]).ravel()
        return x if solve.stats()["success"] else None
