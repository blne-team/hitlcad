"""The model SDK: importable from build123d model code. It knows no part families and no library.

- `mate_to(frame_on_part, frame_on_target)`: the placement that makes a part's connector frame meet
  a target frame, facing it (origins coincide, Z axes opposed, X axes aligned), as a `rigid` mate
  verifies. Use it in assembly.py so placements follow the connectors.
- `CatalogPart`: what catalog providers return for a bought part; a part file returns it as the
  part's value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CatalogPart:
    shape: Any
    connectors: dict[str, Any]
    provider: str
    designation: str
    material: str
    metadata: dict[str, Any] = field(default_factory=dict)
    native: Any = None  # the provider's own object, e.g. for its hole operations


def facing() -> Any:
    """Half a turn about X: the frame of a connector that faces this one."""
    from build123d import Location

    return Location((0, 0, 0), (180, 0, 0))


def mate_to(frame_on_part: Any, frame_on_target: Any) -> Any:
    """The placement P of a part such that P * frame_on_part faces frame_on_target."""
    return frame_on_target * facing() * frame_on_part.inverse()
