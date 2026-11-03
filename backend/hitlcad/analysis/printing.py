"""Printing: each printed part against the project's printer, lying as it prints (`PrintedProcess.up`). Without a
printer (the human enters theirs when they first prepare to print) there is nothing to measure against."""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import ConvexHull

from hitlcad.analysis.base import Context, result
from hitlcad.analysis.coverage import links
from hitlcad.analysis.model import BuiltDesign
from hitlcad.core.checks import PrintedProcess, PrintFacts, PrintSetting, ProcessFinding
from hitlcad.core.report import Result
from hitlcad.kernel.errors import PRINTING_BED


def on_bed(mesh: trimesh.Trimesh, bed: tuple[float, float]) -> tuple[float | None, float, float]:
    """The least turn about Z (degrees, -89 to 90, to the nearest one) at which a part lying as it is fits on a bed
    (X, Y), None where none does; and the length and width of the least rectangle round it, seen from above."""
    points = mesh.vertices[:, :2]
    hull = points[ConvexHull(points).vertices]
    turns = np.radians(np.arange(180))
    cos, sin = np.cos(turns)[:, None], np.sin(turns)[:, None]
    x = hull[:, 0] * cos - hull[:, 1] * sin
    y = hull[:, 0] * sin + hull[:, 1] * cos
    w, h = np.ptp(x, axis=1), np.ptp(y, axis=1)
    fits = np.flatnonzero((w <= bed[0]) & (h <= bed[1]))
    least = int(np.argmin(w * h))
    signed = np.where(fits > 90, fits - 180, fits)
    return (float(signed[np.argmin(np.abs(signed))]) if fits.size else None,
            float(max(w[least], h[least])), float(min(w[least], h[least])))  # fmt: skip


class Printing:
    name = "printing"
    stage = "process"
    title = "Printing"
    summary = "Each printed part, lying as it prints, against the project's printer: it fits on the bed"

    def run(self, ctx: Context) -> list[Result]:
        printer = ctx.built.printer
        if printer is None:
            return []
        x, y, z = printer.build_volume_mm
        out = []
        for name, part in ctx.built.parts.items():
            process = ctx.built.spec.process(name)
            if not isinstance(process, PrintedProcess) or part.mesh.is_empty:
                continue
            mesh = ctx.bodies.made(name, process).mesh
            turn, length, width = on_bed(mesh, (x, y))
            height = float(np.ptp(mesh.bounds[:, 2]))
            fits = turn is not None and height <= z
            detail = (f"it lies {length:.1f} × {width:.1f} mm and stands {height:.1f} mm tall, on the {printer.name}'s "
                      f"{x:g} × {y:g} mm bed, {z:g} mm high")  # fmt: skip
            if turn is None:
                detail += ": it fits on the bed at no angle"
            elif height > z:
                detail += f": {height - z:.1f} mm too tall"
            elif turn:
                detail += f", turned {turn:g}° on the bed"
            out.append(result(self.name, PRINTING_BED, f"{name}: bed", fits, detail, [name],
                              {"length_mm": length, "width_mm": width, "height_mm": height}
                              | ({"turn_deg": turn} if turn is not None else {})))  # fmt: skip
        return out


def plan(built: BuiltDesign, results: list[Result]) -> dict[str, list[PrintSetting]]:
    """Each printed part's settings, as its process gives them (`PrintedProcess.recommend`) from what this build found
    of it: its fits, the screws threading into it, and its process findings."""
    found, _ = links(built)
    out = {}
    for name in built.parts:
        process = built.spec.process(name)
        if not isinstance(process, PrintedProcess):
            continue
        fits = []
        for link in found:
            fit = getattr(link.source, "fit", None)
            if fit is not None and name in (link.a.part.name, link.b.part.name):
                diameter = link.a.measured.values.get("diameter") or link.b.measured.values.get("diameter") or 0.0
                try:
                    fits.append(fit.limits(diameter)[0])
                except ValueError:  # ISO 286 has no such fit at this size: the mate's fit check says so
                    continue
        facts = PrintFacts(
            nozzle_mm=built.printer.nozzle_mm if built.printer else None,
            tightest_fit_mm=min(fits) if fits else None,
            threaded_into=sorted({link.source.address for link in found
                                  if link.kind == "fastener" and link.role == "into" and link.b.part.name == name}),
            findings=[ProcessFinding(name=r.code.removeprefix("manufacturing."), detail=r.detail,
                                     passed=None if r.status == "unverified" else r.status == "pass")
                      for r in results if r.analysis == "manufacturing" and name in r.about],
        )  # fmt: skip
        out[name] = process.recommend(facts)
    return out
