"""Bambu Studio: `print.3mf`, each copy of each part an object of its own with its settings as Bambu Studio keeps an
object's (`Metadata/model_settings.config`), and `print-plan.md`, how to open it and what each part is set to.

Bambu Studio applies an object's settings from a 3MF any program wrote, when the file is opened as a project (File ›
Open Project); imported as geometry only, it keeps each object's filament and drops the rest. It splits an object
placed more than once into objects before it applies their settings, so each copy is written as an object of its
own, sharing its part's meshes. An object's `extruder` is the filament it prints with: its material's AMS slot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple
from xml.etree import ElementTree as ET

from hitlcad.geometry.threemf import MEDIA, ThreeMf

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from hitlcad.core.checks import PrintSetting
    from hitlcad.core.project import Filament, Printer, ToPrint

PRINT_FILE = "print.3mf"
PLAN_FILE = "print-plan.md"
CONFIG = "/Metadata/model_settings.config"
RELATIONSHIP = "http://schemas.hitlcad.dev/2026/slicer-settings"


class _Setting(NamedTuple):
    key: str  # Bambu Studio's name of the setting, as its files keep it
    value: Callable[[float | bool], str]  # as Bambu Studio reads it


#: hitlcad's print settings (`PrintSetting.name`) as Bambu Studio's object settings.
SETTINGS = {
    "layer_mm": _Setting("layer_height", lambda v: f"{float(v):g}"),
    "walls": _Setting("wall_loops", lambda v: str(int(v))),
    "infill_percent": _Setting("sparse_infill_density", lambda v: f"{float(v):g}%"),
    "supports": _Setting("enable_support", lambda v: "1" if v else "0"),
    "brim": _Setting("brim_type", lambda v: "outer_only" if v else "no_brim"),
}


class BambuStudio:
    name = "bambu-studio"
    title = "Bambu Studio"

    def write(self, design: str, parts: list[ToPrint], printer: Printer, out_dir: Path) -> list[tuple[str, str]]:
        model = ThreeMf()
        config = ET.Element("config")
        for part in parts:
            plan = part.plan
            values = {
                SETTINGS[s.name].key: SETTINGS[s.name].value(s.value) for s in plan.settings if s.name in SETTINGS
            }
            filament = printer.materials.get(plan.material or "")
            if filament is not None and filament.slot is not None:
                values["extruder"] = str(filament.slot)
            for k, frame in enumerate(part.copies):
                name = plan.path if len(part.copies) == 1 else f"{plan.path} #{k + 1}"
                placed, meshes = model.place(part.pieces, name, frame)
                obj = ET.SubElement(config, "object", id=str(placed))
                for key, value in {"name": name, **values}.items():
                    ET.SubElement(obj, "metadata", key=key, value=value)
                for mesh, piece in zip(meshes, part.pieces, strict=True):
                    volume = ET.SubElement(obj, "part", id=str(mesh), subtype="normal_part")
                    ET.SubElement(volume, "metadata", key="name", value=piece.name)
        ET.indent(config)
        model.attach(CONFIG, b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(config, "utf-8", xml_declaration=False),
                     RELATIONSHIP)  # fmt: skip
        model.write(out_dir / PRINT_FILE)
        (out_dir / PLAN_FILE).write_text(_page(design, parts, printer), encoding="utf-8")
        return [(PRINT_FILE, MEDIA), (PLAN_FILE, "text/markdown")]


def _page(design: str, parts: list[ToPrint], printer: Printer) -> str:
    """How to open `print.3mf` in Bambu Studio, and each part's settings and why: what the human checks it by."""
    used = {p.plan.material: printer.materials.get(p.plan.material or "") for p in parts}
    lines = [
        f"# Printing {design} on the {printer.name}",
        "",
        f"`{PRINT_FILE}` holds every printed part, as many as the design places, each lying the way it prints, with "
        "its settings and filament as its own.",
        "",
        f"1. In Bambu Studio, **File › Open Project…** and choose `{PRINT_FILE}` (dropped on the window, open it as a "
        "project). Imported as geometry only, each part keeps its filament but loses its other settings.",
        f"2. Choose the {printer.name} with a {printer.nozzle_mm:g} mm nozzle, and these filaments:",
        "",
        *(
            f"   - {_filament(material, filament)}"
            for material, filament in sorted(used.items(), key=lambda m: m[0] or "")
        ),
        "",
        "3. **Arrange all objects** (A) lays the parts out on plates. It turns them only about Z, which keeps each "
        "the way it prints; **Auto orient** would turn them by its own measure, so leave it.",
        "4. Check each part against the table (select it: the object list shows the settings it has of its own), "
        "then slice.",
        "",
        "| Part | Copies | Lies | Filament | Settings |",
        "|---|---|---|---|---|",
    ]
    for part in parts:
        plan = part.plan
        lies = next(w.label for w in plan.ways if w.current)
        settings = ", ".join(s.text for s in plan.settings)
        lines.append(f"| {plan.path} | {plan.count} | {lies} | {_filament(plan.material, used[plan.material])} | "
                     f"{settings} |")  # fmt: skip
    by_hand = [(p.plan.path, s) for p in parts for s in p.plan.settings if s.name not in SETTINGS]
    if by_hand:
        lines += ["", "Bambu Studio is not given these: set them on the part by hand.", ""]
        lines += [f"- {path}: {s.text}" for path, s in by_hand]
    if any(len(p.pieces) > 1 for p in parts):
        lines += ["", "A part in several colours is an object of several parts, one per colour: give each its "
                  "filament in the object list."]  # fmt: skip
    lines += ["", "## Why", ""]
    for part in parts:
        lines += [f"### {part.plan.path}", "", *(_why(s) for s in part.plan.settings), ""]
    return "\n".join(lines)


def _filament(material: str | None, filament: Filament | None) -> str:
    if filament is None:
        return f"{material or 'no material'}: no filament entered for it in the Print tab (filament 1)"
    slot = f", AMS slot {filament.slot}" if filament.slot is not None else ""
    return f"{material}: {filament.profile}{slot}"


def _why(s: PrintSetting) -> str:
    return f"- **{s.text}**: {s.why}"
