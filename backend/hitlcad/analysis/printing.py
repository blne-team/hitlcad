"""Printing: each printed part against the project's printer, lying as it prints (`PrintedProcess.up`). Without a
printer (the human enters theirs when they first prepare to print) there is nothing to measure against."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

import numpy as np
import trimesh
from scipy.spatial import ConvexHull

from hitlcad.analysis.base import Context, result
from hitlcad.analysis.checks import print_way, print_way_key, process_findings
from hitlcad.analysis.coverage import Link
from hitlcad.core import frames as fr
from hitlcad.core.checks import PrintedProcess, PrintFacts, PrintOrientation, PrintWay
from hitlcad.core.report import PlannedPart, Result
from hitlcad.kernel.errors import PRINTING_BED, PRINTING_ORIENTATION

#: How many of a part's stable poses (the likeliest first) the best orientation is chosen from, besides its own.
POSES = 8
#: How close two ways up may be (degrees) and be one.
SAME_DEG = 1.0


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
            ways = orientations(ctx, name, process)
            out.append(self._best_way(name, ways))
            if printer is None:
                continue
            x, y, z = printer.build_volume_mm
            now = next(w for w in ways if w.current)
            (length, width), height, turn = now.footprint_mm, now.height_mm, now.turn_deg
            detail = (f"it lies {length:.1f} × {width:.1f} mm and stands {height:.1f} mm tall, on the {printer.name}'s "
                      f"{x:g} × {y:g} mm bed, {z:g} mm high")  # fmt: skip
            if turn is None:
                detail += ": it fits on the bed at no angle"
            elif height > z:
                detail += f": {height - z:.1f} mm too tall"
            elif turn:
                detail += f", turned {turn:g}° on the bed"
            out.append(result(self.name, PRINTING_BED, f"{name}: bed", now.fits, detail, [name],
                              {"length_mm": length, "width_mm": width, "height_mm": height}
                              | ({"turn_deg": turn} if turn is not None else {})))  # fmt: skip
        return out

    def _best_way(self, name: str, ways: list[PrintOrientation]) -> Result:
        """Whether a part prints the best way up it could: none better (`PrintOrientation.merit`)."""
        best, now = ways[0], next(w for w in ways if w.current)
        if not best.better:
            detail = f"it prints the best way of {len(ways)}, {now.described()}"
            return result(self.name, PRINTING_ORIENTATION, f"{name}: orientation", True, detail, [name])
        worse = ("it does not fit the printer" if now.fits is False and best.fits is not False
                 else f"it does not stay put on the bed ({'; '.join(now.problems)})" if best.stands and not now.stands
                 else f"{', '.join(now.harmed)} would not come out round" if len(now.harmed) > len(best.harmed)
                 else f"{now.shown_support_mm2:.0f} mm² of what shows would print on support"
                 if now.merit[3] > best.merit[3]
                 else f"it needs {now.support_mm2:.0f} mm² of support")  # fmt: skip
        detail = f"printed {face(best.up)} (`up` {list(best.up)}), {best.described()}; {face(now.up)}, as it prints now, {worse}"
        return result(self.name, PRINTING_ORIENTATION, f"{name}: orientation", False, detail, [name],
                      {"support_mm2": now.support_mm2, "best_support_mm2": best.support_mm2}, severity="warning")  # fmt: skip


def face(up: tuple[float, float, float]) -> str:
    """Which way up, as a person reads it: "as modelled", "on its +Y face", or the direction."""
    if np.allclose(up, (0, 0, abs(up[2]))) and up[2] > 0:
        return "as modelled"
    k = int(np.argmax(np.abs(up)))
    if np.count_nonzero(np.round(up, 6)) == 1:
        return f"on its {'-' if up[k] > 0 else '+'}{'XYZ'[k]} face"
    return f"up [{', '.join(f'{v:.3f}' for v in up)}]"


def orientations(ctx: Context, name: str, process: PrintedProcess) -> list[PrintOrientation]:
    """The ways a part can lie on the bed (its own, then its hull's likeliest stable poses), each judged by its process
    as the way it prints is (`print_way`) and measured against the printer: best first by `PrintOrientation.merit`."""
    part, printer = ctx.built.parts[name], ctx.built.printer

    def poses() -> list[list[float]]:
        found, _ = trimesh.poses.compute_stable_poses(part.mesh.convex_hull)
        own = np.asarray(process.up, dtype=float) / np.linalg.norm(process.up)
        ups: list[np.ndarray] = [own]
        for pose in found[:POSES]:
            up = pose[:3, :3].T @ (0.0, 0.0, 1.0)
            if all(fr.angle_deg(up, u) > SAME_DEG for u in ups):
                ups.append(up)
        return [[round(float(v), 6) for v in up] for up in ups[1:]]

    ups = [
        process.up,
        *(tuple(up) for up in ctx.cache.get_or(ctx.cache.key("print poses", part.digest, list(process.up)), poses)),
    ]
    turned = [process if n == 0 else process.model_copy(update={"up": up}) for n, up in enumerate(ups)]
    bed = printer.build_volume_mm if printer else None
    shown = ctx.shown[name]
    key = ctx.cache.key("print orientations", [print_way_key(ctx, name, t) for t in turned], bed,
                        hashlib.sha256(np.packbits(shown).tobytes()).hexdigest())  # fmt: skip

    def judged() -> list[dict[str, Any]]:
        out = []
        for n, way_up in enumerate(turned):
            way = print_way(ctx, name, way_up)
            mesh = ctx.bodies.made(name, way_up).mesh
            height = float(np.ptp(mesh.bounds[:, 2]))
            turn, length, width = on_bed(mesh.vertices, bed[:2] if bed else (np.inf, np.inf))
            fits = None if bed is None else height <= bed[2] and turn is not None
            out.append(PrintOrientation(up=way_up.up, label=face(way_up.up), fits=fits, stands=way.stands, harmed=way.harmed,
                                        height_mm=height, footprint_mm=(length, width), turn_deg=turn if bed else None,
                                        support_mm2=_area(mesh, way, "support"), bed_mm2=_area(mesh, way, "bed"),
                                        shown_support_mm2=_area(mesh, way, "support", shown),
                                        shown_bed_mm2=_area(mesh, way, "bed", shown),
                                        problems=[f.detail for f in way.findings if f.passed is False], current=n == 0))  # fmt: skip
        now = out[0].merit
        ordered = sorted(out, key=lambda w: (*w.merit, w.height_mm))
        return [w.model_copy(update={"better": w.merit < now}).model_dump(mode="json") for w in ordered]

    return [PrintOrientation.model_validate(w) for w in ctx.cache.get_or(key, judged)]


def _area(mesh: trimesh.Trimesh, way: PrintWay, shade: str, among: np.ndarray | None = None) -> float:
    """The area of what a way's findings shade so ('support', 'bed'), of the faces `among` (a boolean per face) if
    given."""
    faces = np.array(sorted({f for x in way.findings for f in x.faces.get(shade, [])}), dtype=int)
    if among is not None:
        faces = faces[among[faces]]
    return float(mesh.area_faces[faces].sum())


def plan(ctx: Context, components: dict[str, list[PlannedPart]]) -> list[PlannedPart]:
    """A design's print plan: each printed part of its own, with how many the design places, the ways it can lie,
    and its settings as its process gives them (`PrintedProcess.recommend`) from what this build found of it (its
    fits, the screws threading into it, its process findings); then each component's own plan, its counts times
    the component's."""
    built = ctx.built
    placed = Counter(name for _, name, _ in built.instances())
    found = ctx.links
    out = []
    for name, part in built.parts.items():
        process = built.spec.process(name)
        if not isinstance(process, PrintedProcess) or not placed[name]:
            continue
        out.append(PlannedPart(path=name, count=placed[name], material=part.material, process=process.process,
                               ways=orientations(ctx, name, process),
                               settings=process.recommend(_facts(ctx, name, process, found))))  # fmt: skip
    for name, parts in components.items():
        out += [p.model_copy(update={"path": f"{name}/{p.path}", "count": p.count * placed[name]}) for p in parts
                if placed[name]]  # fmt: skip
    return out


def _facts(ctx: Context, name: str, process: PrintedProcess, found: list[Link]) -> PrintFacts:
    """What this build knows of a printed part, for its process to recommend its settings by."""
    built = ctx.built
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
        findings=process_findings(ctx, name, process),
    )  # fmt: skip
