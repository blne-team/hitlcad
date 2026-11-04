"""The material registry: density, the processes a material suits, the minimum thread engagement (in nominal
diameters) a screw needs in it, and what it takes in service: how hot, and whether it flexes.

Plugins give materials.json files (hitlcad's own: `hitlcad.builtin.materials`); a design sees those of hitlcad's
own and its project's plugins. A project adds or overrides materials with its own `materials.json`: the nearest one
in a design's folder or its parents.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import Field, TypeAdapter

from hitlcad.kernel.model import Model
from hitlcad.kernel.plugins import registry

FILE = "materials.json"


class Material(Model):
    density_g_cm3: float = Field(gt=0)
    processes: list[str] = Field(default_factory=list)
    min_thread_engagement_d: float = Field(
        gt=0, description="Minimum engagement of a screw thread in it, × nominal diameter"
    )
    service_temperature_c: float | None = Field(
        default=None,
        description="The hottest it keeps its shape at under a light load: its heat deflection temperature at "
        "0.45 MPa (ISO 75); none where the registry does not know it",
    )
    flexes: bool | None = Field(
        default=None,
        description="It takes being flexed again and again (a snap, a clip, a living hinge) without cracking: it "
        "yields well before it breaks; none where the registry does not know",
    )


_ADAPTER: TypeAdapter[dict[str, Material]] = TypeAdapter(dict[str, Material])


def builtin(plugins: Mapping[str, str]) -> dict[str, Material]:
    """The materials of hitlcad's own plugins and of `plugins`, a later file's replacing an earlier one's of the same
    name."""
    out: dict[str, Material] = {}
    for path in registry().materials:
        if registry().is_active(path, plugins):
            out |= _ADAPTER.validate_json(path.read_text(encoding="utf-8"))
    return out


def for_design(folder: Path, plugins: Mapping[str, str]) -> dict[str, Material]:
    """The registry a design with these plugins sees: their materials, overridden by the nearest project
    materials.json."""
    found = builtin(plugins)
    for parent in [folder, *folder.parents]:
        if (parent / FILE).is_file():
            found |= _ADAPTER.validate_json((parent / FILE).read_text(encoding="utf-8"))
            break
    return found


def names(registry: dict[str, Material]) -> str:
    return ", ".join(sorted(registry))
