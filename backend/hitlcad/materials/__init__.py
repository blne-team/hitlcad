"""The material registry: density, the processes a material suits, and the minimum thread engagement
(in nominal diameters) a screw needs in it.

hitlcad ships `materials.json`. A project adds or overrides materials with its own `materials.json`:
the nearest one in a design's folder or its parents.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from pydantic import Field, TypeAdapter

from hitlcad.core.types import Model

FILE = "materials.json"


class Material(Model):
    density_g_cm3: float = Field(gt=0)
    processes: list[str] = Field(default_factory=list)
    min_thread_engagement_d: float = Field(
        gt=0, description="Minimum engagement of a screw thread in it, × nominal diameter"
    )


_ADAPTER: TypeAdapter[dict[str, Material]] = TypeAdapter(dict[str, Material])


def builtin() -> dict[str, Material]:
    return _ADAPTER.validate_json(files(__name__).joinpath(FILE).read_text(encoding="utf-8"))


def for_design(folder: Path) -> dict[str, Material]:
    """The registry a design sees: hitlcad's materials, overridden by the nearest project materials.json."""
    registry = builtin()
    for parent in [folder, *folder.parents]:
        if (parent / FILE).is_file():
            registry |= _ADAPTER.validate_json((parent / FILE).read_text(encoding="utf-8"))
            break
    return registry


def names(registry: dict[str, Material]) -> str:
    return ", ".join(sorted(registry))
