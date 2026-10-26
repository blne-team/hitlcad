"""Bills of materials: indented (components with their parts beneath) as CSV and JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hitlcad.core.report import BomLine

if TYPE_CHECKING:
    from hitlcad.analysis.model import BuiltDesign

COLUMNS = list(BomLine.model_fields)


class BomExporter:
    name = "bom"
    title = "Bill of materials"
    summary = "bom.csv and bom.json: every part and component with quantity, material, process and what to buy"

    def export(self, built: BuiltDesign, report: dict[str, Any], out_dir: Path) -> list[tuple[str, str]]:
        lines: list[BomLine] = report["bom"]
        with (out_dir / "bom.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(line.model_dump() for line in lines)
        (out_dir / "bom.json").write_text(json.dumps([line.model_dump() for line in lines], indent=2), encoding="utf-8")
        return [("bom.csv", "text/csv"), ("bom.json", "application/json")]
