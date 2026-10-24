"""The model SDK, for model code in any backend. It knows no part families and no library, and imports
nothing, so Blender's own Python loads it too.

- `mate_to(frame_on_part, frame_on_target)`: the placement that makes a part's connector frame meet
  a target frame, facing it (origins coincide, Z axes opposed, X axes aligned), as a `rigid` mate
  verifies. Use it in assembly.py so placements follow the connectors. Frames are whatever the
  backend's code uses: build123d Locations, Blender's mathutils Matrices, or 4 × 4 nested lists; the
  placement comes back as the same.
- `CatalogPart`: what catalog providers return for a bought part; a part file returns it as the
  part's value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Half a turn about X: the frame of a connector that faces one at the identity.
FACING = ((1.0, 0.0, 0.0, 0.0), (0.0, -1.0, 0.0, 0.0), (0.0, 0.0, -1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


@dataclass
class CatalogPart:
    shape: Any
    connectors: dict[str, Any]
    provider: str
    designation: str
    material: str
    metadata: dict[str, Any] = field(default_factory=dict)
    native: Any = None  # the provider's own object, e.g. for its hole operations


def mate_to(frame_on_part: Any, frame_on_target: Any) -> Any:
    """The placement P of a part such that P * frame_on_part faces frame_on_target."""
    placed = _product(_product(_rows(frame_on_target), FACING), _inverse(_rows(frame_on_part)))
    return _like(frame_on_target, placed)


def _rows(frame: Any) -> list[list[float]]:
    """A Location, a Matrix or nested rows, as 4 × 4 rows."""
    if hasattr(frame, "wrapped"):  # a build123d Location
        t = frame.wrapped.Transformation()
        return [[t.Value(r, c) for c in range(1, 5)] for r in range(1, 4)] + [[0.0, 0.0, 0.0, 1.0]]
    return [[float(v) for v in row] for row in frame]


def _like(frame: Any, rows: list[list[float]]) -> Any:
    """`rows` as the kind of frame `frame` is."""
    if hasattr(frame, "wrapped"):
        from build123d import Location
        from OCP.gp import gp_Trsf

        t = gp_Trsf()
        t.SetValues(*(v for row in rows[:3] for v in row))
        return Location(t)
    if type(frame).__module__ == "mathutils":
        return type(frame)(rows)
    return rows


def _product(a: Any, b: Any) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _inverse(m: list[list[float]]) -> list[list[float]]:
    """The inverse of a rigid frame: its rotation transposed, its translation turned back."""
    r = [[m[c][r] for c in range(3)] for r in range(3)]
    t = [-sum(r[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [[*r[i], t[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
