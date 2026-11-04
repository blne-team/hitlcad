"""What a design gets from its project (the nearest `hitlcad.toml` above its folder): the plugins it names, and the
printer its parts are printed on.

hitlcad defines no printer: the human enters theirs when they first prepare a design for printing, and the project
keeps it (`[printer]`). What a plugin gives is a slicer (`Slicer`, through `hitlcad_slicers`): an adapter to one slicer
program, which writes the file the human opens in it, each part with its settings in that slicer's own terms.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol

from pydantic import Field, PositiveFloat

from hitlcad.kernel.model import Model

if TYPE_CHECKING:
    from hitlcad.core import frames as fr
    from hitlcad.core.report import PlannedPart
    from hitlcad.geometry.cadview import Piece


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


class ToPrint(NamedTuple):
    """A printed part as its slicer's file takes it: what the print plan says of it (how many, of what, with which
    settings), its pieces (one per colour), and each copy lying as it prints on the bed, side by side."""

    plan: PlannedPart
    pieces: list[Piece]
    copies: list[fr.Frame]


class Slicer(Protocol):
    """A slicer program, as a plugin adapts it (`hitlcad_slicers`)."""

    name: str  # what a printer's `slicer` names: "bambu-studio"
    title: str  # "Bambu Studio"

    def write(self, design: str, parts: list[ToPrint], printer: Printer, out_dir: Path) -> list[tuple[str, str]]:
        """The file the human opens in this slicer: every copy of every part, lying as it prints, with its settings
        and its filament as its own; and a page saying how to open it and what each part is set to, and why. Their
        paths under `out_dir` and media types."""
        ...
