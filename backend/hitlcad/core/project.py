"""What a design gets from its project (the nearest `hitlcad.toml` above its folder): the plugins it names, and the
printer its parts are printed on.

hitlcad defines no printer: the human enters theirs when they first prepare a design for printing, and the project
keeps it (`[printer]`). What a plugin gives is a slicer (`Slicer`, through `hitlcad_slicers`): an adapter to one slicer
program, which says how to print a part in that slicer's own terms.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import Field, PositiveFloat

from hitlcad.kernel.model import Model


class Filament(Model):
    """The filament a printer prints one of hitlcad's materials with."""

    profile: str = Field(description="The slicer's filament profile, as the slicer names it: 'Bambu PLA Basic'")
    slot: int | None = Field(default=None, ge=1, description="Its slot in the printer's AMS, if it has one")


class Printer(Model):
    """The human's printer, as they entered it."""

    name: str = Field(description="What the human calls it: 'Bambu Lab P2S'")
    build_volume_mm: tuple[PositiveFloat, PositiveFloat, PositiveFloat] = Field(
        description="Its bed (X, Y) and the height it prints to (Z)"
    )
    nozzle_mm: float = Field(gt=0)
    slicer: str = Field(description="The slicer it is sliced in: a `Slicer`'s name")
    materials: dict[str, Filament] = Field(default_factory=dict, description="hitlcad's material -> its filament")


class ProjectSettings(Model):
    """What a design folder gets from its project's `hitlcad.toml`."""

    plugins: dict[str, str] = Field(default_factory=dict, description="The plugins it names beyond hitlcad's own, "
                                    "at their installed versions: distribution -> version")  # fmt: skip
    printer: Printer | None = None


class Slicer(Protocol):
    """A slicer program, as a plugin adapts it (`hitlcad_slicers`)."""

    name: str  # what a printer's `slicer` names: "bambu-studio"
    title: str  # "Bambu Studio"
