"""Catalog providers: where bought parts' geometry, connectors and data come from.

A provider is a module model code imports, with helpers suited to its library, plus an object
implementing `CatalogProvider` that `search_catalog` searches. Providers register under the
``hitlcad.catalogs`` entry-point group; `bd_warehouse` (standard fasteners, inserts and bearings) and
`step` (vendor STEP files) ship with hitlcad.
"""

from __future__ import annotations

import logging
from functools import cache
from importlib.metadata import entry_points
from typing import Protocol

from pydantic import Field

from hitlcad.core.types import Model

log = logging.getLogger(__name__)


class CatalogItem(Model):
    provider: str
    family: str = Field(description="screw, nut, washer, insert, bearing, …")
    designation: str
    call: str = Field(description="How model code makes it")
    sizes: list[str] = Field(default_factory=list)
    connectors: dict[str, str] = Field(default_factory=dict, description="Connector -> kind and meaning")
    material: str | None = None


class CatalogProvider(Protocol):
    name: str
    #: How to import it in model code, e.g. "from hitlcad.catalogs.bd_warehouse import screw".
    usage: str

    def search(self, query: str, limit: int) -> list[CatalogItem]: ...


def _builtin() -> dict[str, CatalogProvider]:
    from hitlcad.catalogs.bd_warehouse import PROVIDER as bd_warehouse
    from hitlcad.catalogs.step import PROVIDER as step

    return {bd_warehouse.name: bd_warehouse, step.name: step}


@cache
def providers() -> dict[str, CatalogProvider]:
    found = _builtin()
    for ep in entry_points(group="hitlcad.catalogs"):
        try:
            found[ep.name] = ep.load()
        except Exception:  # a broken plugin must not take the service down
            log.exception("failed to load catalog provider %s", ep.name)
    return found


def search(query: str, limit: int) -> list[CatalogItem]:
    """Every provider's best matches, interleaved so no provider crowds out the others."""
    per = [p.search(query, limit) for p in providers().values()]
    merged = [results[rank] for rank in range(limit) for results in per if rank < len(results)]
    return merged[:limit]
