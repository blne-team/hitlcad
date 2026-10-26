"""Catalog providers: where bought parts' geometry, connectors and data come from.

A provider is a module model code imports, with helpers suited to its library (a plugin's model package), plus
an object implementing `CatalogProvider` that `search_catalog` searches, which the plugin gives from its
`hitlcad_catalogs` hook. hitlcad's own (`hitlcad.builtin.catalogs`) are `bd_warehouse` (standard fasteners,
inserts and bearings) and `step` (vendor STEP files).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from hitlcad.kernel.model import Model
from hitlcad.kernel.plugins import registry


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
    #: How to import it in model code, e.g. "from hitlcad.builtin.catalogs.bd_warehouse import screw".
    usage: str

    def search(self, query: str, limit: int) -> list[CatalogItem]: ...


def providers() -> dict[str, CatalogProvider]:
    return registry().catalogs


def search(query: str, limit: int) -> list[CatalogItem]:
    """Every provider's best matches, interleaved so no provider crowds out the others."""
    per = [p.search(query, limit) for p in providers().values()]
    merged = [results[rank] for rank in range(limit) for results in per if rank < len(results)]
    return merged[:limit]
