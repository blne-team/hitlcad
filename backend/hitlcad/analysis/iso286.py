"""ISO 286 fits, from the isofits package's tables: the diametral clearance range of a hole/shaft fit."""

from __future__ import annotations

import re

_FIT = re.compile(r"^\s*([A-Z]{1,2}\d{1,2})\s*/\s*([a-z]{1,2}\d{1,2})\s*$")


def _classes(fit: str) -> tuple[str, str]:
    m = _FIT.match(fit)
    if not m:
        raise ValueError(f"{fit!r} is not an ISO 286 fit like 'H7/g6' (hole class / shaft class)")
    return m.group(1), m.group(2)


def fit_clearance(fit: str, nominal_mm: float) -> tuple[float, float]:
    """(least, greatest) diametral clearance in mm of `fit` at `nominal_mm`; negative is interference."""
    import isofits

    hole, shaft = _classes(fit)
    hole_hi, hole_lo = isofits.isotol("hole", nominal_mm, hole, "both")
    shaft_hi, shaft_lo = isofits.isotol("shaft", nominal_mm, shaft, "both")
    return ((hole_lo - shaft_hi) / 1000.0, (hole_hi - shaft_lo) / 1000.0)
