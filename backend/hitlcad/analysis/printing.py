"""Printing: each printed part against the project's printer, lying as it prints (`PrintedProcess.up`). Without a
printer (the human enters theirs when they first prepare to print) there is nothing to measure against."""

from __future__ import annotations

from collections import Counter

import numpy as np
import trimesh
from scipy.spatial import ConvexHull

from hitlcad.analysis.base import Context, ResultCache, result
from hitlcad.analysis.coverage import Link, links
from hitlcad.analysis.model import BuiltDesign, PartGeometry
from hitlcad.core import frames as fr
from hitlcad.core.checks import PrintedProcess, PrintFacts, PrintOrientation, ProcessFinding
from hitlcad.core.project import Printer
from hitlcad.core.report import PlannedPart, Result
from hitlcad.kernel.errors import PRINTING_BED, PRINTING_ORIENTATION

#: How many of a part's stable poses (the likeliest first) the best orientation is chosen from, besides its own.
POSES = 8
#: How close two ways up may be (degrees) and be one.
SAME_DEG = 1.0
#: How far above the lowest point a face may lie and still be on the bed, mm.
ON_BED_MM = 0.05


def on_bed(vertices: np.ndarray, bed: tuple[float, float]) -> tuple[float | None, float, float]:
    """The least turn about Z (degrees, -89 to 90, to the nearest one) at which a part lying as its vertices do fits
    on a bed (X, Y), None where none does; and the length and width of the least rectangle round it, from above."""
    points = vertices[:, :2]
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
    summary = "Each printed part, lying as it prints: the best way up for it, and that it fits the project's printer"

    def run(self, ctx: Context) -> list[Result]:
        printer = ctx.built.printer
        out = []
        for name, part in ctx.built.parts.items():
            process = ctx.built.spec.process(name)
            if not isinstance(process, PrintedProcess) or part.mesh.is_empty:
                continue
            out.append(self._best_way(name, orientations(part, process, printer, ctx.cache)))
            if printer is None:
                continue
            x, y, z = printer.build_volume_mm
            mesh = ctx.bodies.made(name, process).mesh
            turn, length, width = on_bed(mesh.vertices, (x, y))
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

    def _best_way(self, name: str, ways: list[PrintOrientation]) -> Result:
        """Whether a part prints the best way up its stable poses offer: less support, or fitting the printer where it
        does not now."""
        best, now = ways[0], next(w for w in ways if w.current)
        better = best.overhang_mm2 < now.overhang_mm2 - 10 or (now.fits is False and best.fits is True)
        if best.current or not better:
            detail = (f"it prints the best way of {len(ways)}, {face(now.up)}: {now.overhang_mm2:.0f} mm² over nothing, "
                      f"{now.bed_mm2:.0f} mm² on the bed, {now.height_mm:.1f} mm tall")  # fmt: skip
            return result(self.name, PRINTING_ORIENTATION, f"{name}: orientation", True, detail, [name])
        detail = (f"printed {face(best.up)} (`up` {list(best.up)}) it needs {best.overhang_mm2:.0f} mm² of support, "
                  f"{best.bed_mm2:.0f} mm² on the bed, {best.height_mm:.1f} mm tall; {face(now.up)}, as it prints now, "
                  f"{now.overhang_mm2:.0f} mm² of support" + (", and it does not fit the printer" if now.fits is False else ""))  # fmt: skip
        return result(self.name, PRINTING_ORIENTATION, f"{name}: orientation", False, detail, [name],
                      {"support_mm2": now.overhang_mm2, "best_support_mm2": best.overhang_mm2}, severity="warning")  # fmt: skip


def face(up: tuple[float, float, float]) -> str:
    """Which way up, as a person reads it: "as modelled", "on its +Y face", or the direction."""
    if np.allclose(up, (0, 0, abs(up[2]))) and up[2] > 0:
        return "as modelled"
    k = int(np.argmax(np.abs(up)))
    if np.count_nonzero(np.round(up, 6)) == 1:
        return f"on its {'-' if up[k] > 0 else '+'}{'XYZ'[k]} face"
    return f"up [{', '.join(f'{v:.3f}' for v in up)}]"


def orientations(part: PartGeometry, process: PrintedProcess, printer: Printer | None,
                 cache: ResultCache) -> list[PrintOrientation]:  # fmt: skip
    """The ways a part can lie on the bed (its own and the stable poses of its hull, the likeliest first), scored
    and best first: the least support, then fitting the printer, then the most on the bed, then the lowest."""
    limit = process.overhang_limit_deg()
    volume = printer.build_volume_mm if printer else None
    key = cache.key("print orientations", part.digest, limit, list(process.up), volume)

    def compute() -> list[dict[str, object]]:
        mesh = part.mesh
        poses, _ = trimesh.poses.compute_stable_poses(mesh.convex_hull)
        own = np.asarray(process.up, dtype=float) / np.linalg.norm(process.up)
        ups: list[np.ndarray] = [own]
        for pose in poses[:POSES]:
            up = pose[:3, :3].T @ (0.0, 0.0, 1.0)
            if all(fr.angle_deg(up, u) > SAME_DEG for u in ups):
                ups.append(up)
        out = []
        for up in ups:
            turned = process.model_copy(update={"up": tuple(float(v) for v in up)}).made()
            vertices = fr.transform_points(mesh.vertices, turned)
            normals = mesh.face_normals @ turned[:3, :3].T
            low = vertices[:, 2].min()
            resting = (vertices[mesh.faces][:, :, 2].max(axis=1) <= low + ON_BED_MM) & (normals[:, 2] < -0.999)
            steep = (normals[:, 2] < -np.sin(np.radians(limit))) & ~resting
            height = float(np.ptp(vertices[:, 2]))
            fits = None if volume is None else height <= volume[2] and on_bed(vertices, volume[:2])[0] is not None
            way = tuple(round(float(v), 6) for v in up)
            out.append({"up": list(way), "label": face(way), "overhang_mm2": float(mesh.area_faces[steep].sum()),
                        "bed_mm2": float(mesh.area_faces[resting].sum()), "height_mm": height, "fits": fits,
                        "current": up is own})  # fmt: skip
        out.sort(key=lambda o: (round(o["overhang_mm2"], -1), o["fits"] is False, -round(o["bed_mm2"], -1),
                                o["height_mm"]))  # type: ignore[arg-type,operator]  # fmt: skip
        return out

    return [PrintOrientation.model_validate(o) for o in cache.get_or(key, compute)]


def plan(built: BuiltDesign, results: list[Result], cache: ResultCache,
         components: dict[str, list[PlannedPart]]) -> list[PlannedPart]:  # fmt: skip
    """A design's print plan: each printed part of its own, with how many the design places, the ways it can lie,
    and its settings as its process gives them (`PrintedProcess.recommend`) from what this build found of it (its
    fits, the screws threading into it, its process findings); then each component's own plan, its counts times
    the component's."""
    placed = Counter(name for _, name, _ in built.instances())
    found, _ = links(built)
    out = []
    for name, part in built.parts.items():
        process = built.spec.process(name)
        if not isinstance(process, PrintedProcess) or not placed[name]:
            continue
        out.append(PlannedPart(path=name, count=placed[name], material=part.material, process=process.process,
                               ways=orientations(part, process, built.printer, cache),
                               settings=process.recommend(_facts(built, name, found, results))))  # fmt: skip
    for name, parts in components.items():
        out += [p.model_copy(update={"path": f"{name}/{p.path}", "count": p.count * placed[name]}) for p in parts
                if placed[name]]  # fmt: skip
    return out


def _facts(built: BuiltDesign, name: str, found: list[Link], results: list[Result]) -> PrintFacts:
    """What this build knows of a printed part, for its process to recommend its settings by."""
    fits = []
    for link in found:
        fit = getattr(link.source, "fit", None)
        if fit is not None and name in (link.a.part.name, link.b.part.name):
            diameter = link.a.measured.values.get("diameter") or link.b.measured.values.get("diameter") or 0.0
            try:
                fits.append(fit.limits(diameter)[0])
            except ValueError:  # ISO 286 has no such fit at this size: the mate's fit check says so
                continue
    return PrintFacts(
        nozzle_mm=built.printer.nozzle_mm if built.printer else None,
        tightest_fit_mm=min(fits) if fits else None,
        threaded_into=sorted({link.source.address for link in found
                              if link.kind == "fastener" and link.role == "into" and link.b.part.name == name}),
        findings=[ProcessFinding(name=r.code.removeprefix("manufacturing."), detail=r.detail,
                                 passed=None if r.status == "unverified" else r.status == "pass")
                  for r in results if r.analysis == "manufacturing" and name in r.about],
    )  # fmt: skip
