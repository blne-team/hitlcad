"""A project's file, `hitlcad.toml` at its root: what its designs get from it (`ProjectSettings`), the nearest one in a
design's folder or its parents.

    [plugins]                       the installed plugins its designs use beyond hitlcad's own; its agent keeps them
    hitlcad-lasercut = ">=0.1"

    [printer]                       the human's printer, entered in the panel when they first prepare to print
    name = "Bambu Lab P2S"
    build_volume_mm = [256, 256, 256]
    nozzle_mm = 0.4
    slicer = "bambu-studio"
    [printer.materials.PLA]
    profile = "Bambu PLA Basic"
    slot = 1
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import ValidationError

from hitlcad.core.project import Printer, ProjectSettings
from hitlcad.kernel.errors import PROJECT_INVALID
from hitlcad.kernel.plugins import registry

PROJECT_FILE = "hitlcad.toml"


def find(folder: Path) -> Path | None:
    """The project file of a design folder: the nearest in it or its parents."""
    return next((p / PROJECT_FILE for p in [folder, *folder.parents] if (p / PROJECT_FILE).is_file()), None)


def _read(file: Path) -> tuple[dict[str, Any], Printer | None]:
    try:
        doc = tomllib.loads(file.read_text(encoding="utf-8"))
        return doc, Printer.model_validate(doc["printer"]) if "printer" in doc else None
    except (tomllib.TOMLDecodeError, ValidationError) as e:
        raise PROJECT_INVALID(f"{file}: {e}") from None


def printer(folder: Path) -> Printer | None:
    """The printer of a design folder's project: none yet, or outside any project."""
    file = find(folder)
    return _read(file)[1] if file else None


def settings(folder: Path) -> ProjectSettings:
    """What a design folder gets from its project: none of a project's plugins and no printer outside any."""
    file = find(folder)
    if file is None:
        return ProjectSettings()
    doc, printer = _read(file)
    return ProjectSettings(plugins=registry().installed(doc.get("plugins", {}), str(file)), printer=printer)


def set_printer(file: Path, printer: Printer) -> None:
    """Write the project's printer, keeping the rest of its file as it is."""
    doc = tomlkit.parse(file.read_text(encoding="utf-8"))
    doc["printer"] = printer.model_dump(mode="json", exclude_none=True)
    file.write_text(tomlkit.dumps(doc), encoding="utf-8")
