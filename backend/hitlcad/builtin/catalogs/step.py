"""Vendor parts from STEP files: a motor, a connector, a board, as its maker publishes it.

In model code:

    from hitlcad.catalogs import step

    def build(p, i):
        return {"motor": step.part("vendor/nema17.step", sha256="…", designation="NEMA 17 stepper",
                                   material="steel", connectors={"face": Location(...), ...})}

The file must match its checksum, so a changed vendor file is noticed. A vendor STEP defines no
connectors of its own: the part file gives them, and design.json declares them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from hitlcad.catalogs import CatalogItem
from hitlcad.sdk import CatalogPart

NAME = "step"


def part(path: str, *, sha256: str, designation: str, material: str, connectors: dict[str, Any],
         metadata: dict[str, Any] | None = None) -> CatalogPart:  # fmt: skip
    """A vendor STEP (a path relative to the design folder) as a catalog part."""
    from build123d import import_step

    file = Path(path)
    if file.is_absolute() or ".." in file.parts:
        raise ValueError(f"vendor file {path!r} must be inside the design folder")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    if digest != sha256:
        raise ValueError(f"{path} has checksum {digest}, not the {sha256} the part file expects")
    return CatalogPart(shape=import_step(str(file)), connectors=connectors, provider=NAME, designation=designation,
                       material=material, metadata=metadata or {})  # fmt: skip


class StepProvider:
    name = NAME
    usage = "from hitlcad.catalogs import step; step.part('vendor/<file>.step', sha256=…, …)"

    def search(self, query: str, limit: int) -> list[CatalogItem]:
        return []  # vendor files are the project's own: nothing to search


PROVIDER = StepProvider()
