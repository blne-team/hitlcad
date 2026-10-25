"""Manufacturability checks of the manufacturing processes, on one made part in its own frame (the way it
is made). Each process's thresholds are fields of its model in `core.checks`; nothing here is a
tolerance of its own. Findings are warnings: a part that is hard to make still builds, and the agent
and the human see why.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import numpy as np
import trimesh

from hitlcad.analysis.features import Features, InsideCorner, Unmeasurable
from hitlcad.core.checks import (
    CncMillingProcess,
    FdmProcess,
    InjectionMoldingProcess,
    ProcessFinding,
    ResinProcess,
    SheetMetalProcess,
)

if TYPE_CHECKING:
    from hitlcad.analysis.model import Body

#: A wall's far side faces back within this angle; a ray that leaves through anything else crossed a
#: corner, not a wall. This defines what wall thickness means, not a tolerance.
WALL_EXIT_DEG = 60
#: Rays × triangles cast at once. trimesh's ray engine holds every candidate (ray, triangle) pair of a cast,
#: a few dozen bytes each: all 4000 rays through a 100k-triangle figurine at once took 8.6 GB.
RAY_BATCH = 10_000_000


def _fmt(p: Any) -> str:
    return "(" + ", ".join(f"{c:.1f}" for c in p) + ")"


# Even sampling places fewer points than asked on some parts, which measures their walls as well; trimesh
# would warn about it on every build.
logging.getLogger("trimesh.sample").setLevel(logging.ERROR)


def wall_thickness(mesh: trimesh.Trimesh, samples: int) -> tuple[np.ndarray, np.ndarray]:
    """Points spread evenly over the surface and the wall thickness straight through the part at each
    (inf: not measured, where the ray left through a face not turned back towards it)."""
    points, faces = trimesh.sample.sample_surface_even(mesh, samples, seed=0)
    inward = -mesh.face_normals[faces]
    step = max(1, RAY_BATCH // len(mesh.faces))
    casts = [(start, *mesh.ray.intersects_id(points[start : start + step], inward[start : start + step],
                                             return_locations=True, multiple_hits=True))
             for start in range(0, len(points), step)]  # fmt: skip
    hit = np.concatenate([h for _, h, _, _ in casts])
    ray = np.concatenate([start + r for start, _, r, _ in casts])
    location = np.concatenate([at for _, _, _, at in casts])
    distance = np.linalg.norm(location - points[ray], axis=1)
    beyond_start = distance > trimesh.tol.planar
    hit, ray, distance = hit[beyond_start], ray[beyond_start], distance[beyond_start]
    order = np.lexsort((distance, ray))
    ray, first = np.unique(ray[order], return_index=True)  # each ray's exit: its nearest hit
    hit, distance = hit[order][first], distance[order][first]
    through_wall = np.einsum("ij,ij->i", mesh.face_normals[hit], inward[ray]) >= math.cos(math.radians(WALL_EXIT_DEG))
    thickness = np.full(len(points), np.inf)
    thickness[ray[through_wall]] = distance[through_wall]
    return points, thickness


def _on_bed(mesh: trimesh.Trimesh, tolerance: float) -> np.ndarray:
    return mesh.triangles[:, :, 2].max(axis=1) <= mesh.bounds[0][2] + tolerance


# --------------------------------------------------------------------------- FDM


def fdm_checks(body: Body, rules: FdmProcess) -> list[ProcessFinding]:
    mesh = body.mesh
    if mesh.is_empty:
        return []
    walls = _walls(body, "fdm_wall_thickness", rules.min_wall_mm, (rules.nozzle_mm, "nozzle"))
    return [walls, _fdm_overhangs(body, rules), _fdm_bed(body, rules)]


def _walls(body: Body, name: str, minimum: float, cannot: tuple[float, str] | None = None) -> ProcessFinding:
    """No wall thinner than `minimum`; under `cannot` (a size and what it is), the wall does not form at all."""
    points, thickness = wall_thickness(body.mesh, body.settings.wall_samples)
    measured = np.isfinite(thickness)
    if not measured.any():
        return ProcessFinding(name=name, passed=None, detail="not measurable: the mesh is not closed")
    thinnest = int(np.argmin(np.where(measured, thickness, np.inf)))
    value = float(thickness[thinnest])
    if value >= minimum:
        return ProcessFinding(name=name, passed=True, detail=f"thinnest wall {value:.2f} mm (minimum {minimum:g} mm)")
    thin_share = float(np.mean(thickness[measured] < minimum))
    worse = (
        f", thinner than the {cannot[0]:g} mm {cannot[1]}: it will not print" if cannot and value < cannot[0] else ""
    )
    share = f"{thin_share:.0%}" if thin_share >= 0.01 else "under 1%"
    return ProcessFinding(name=name, passed=False,
                          detail=f"thinnest wall {value:.2f} mm at {_fmt(points[thinnest])}{worse}; {share} of the "
                                 f"surface is under the {minimum:g} mm minimum")  # fmt: skip


def _fdm_overhangs(body: Body, rules: FdmProcess) -> ProcessFinding:
    mesh = body.mesh
    steep = mesh.face_normals[:, 2] < -math.sin(math.radians(rules.max_overhang_deg))
    needs_support = steep & ~_on_bed(mesh, body.settings.mesh_tolerance_mm)
    area = float(mesh.area_faces[needs_support].sum())
    if area <= rules.ignored_overhang_mm2:
        return ProcessFinding(name="fdm_overhangs", passed=True,
                              detail=f"no overhang steeper than {rules.max_overhang_deg:g}° from vertical")  # fmt: skip
    lowest = mesh.triangles_center[needs_support][np.argmin(mesh.triangles_center[needs_support][:, 2])]
    if rules.supports:
        return ProcessFinding(name="fdm_overhangs", passed=True,
                              detail=f"{area:.0f} mm² overhangs more than {rules.max_overhang_deg:g}° from vertical and "
                                     f"prints on support (lowest at {_fmt(lowest)})")  # fmt: skip
    return ProcessFinding(name="fdm_overhangs", passed=False,
                          detail=f"{area:.0f} mm² overhangs more than {rules.max_overhang_deg:g}° from vertical and "
                                 f"needs support (lowest at {_fmt(lowest)}); chamfer it, turn the part in its own "
                                 "frame or split it")  # fmt: skip


def _fdm_bed(body: Body, rules: FdmProcess) -> ProcessFinding:
    mesh = body.mesh
    flat_down = mesh.face_normals[:, 2] < -math.cos(math.radians(body.settings.tolerances.angle_deg))
    contact = float(mesh.area_faces[flat_down & _on_bed(mesh, body.settings.mesh_tolerance_mm)].sum())
    (x0, y0, _), (x1, y1, _) = mesh.bounds
    footprint = (x1 - x0) * (y1 - y0)
    share = contact / footprint if footprint > 0 else 0.0
    ok = share >= rules.min_bed_contact_share
    lowest = mesh.vertices[np.argmin(mesh.vertices[:, 2])]
    return ProcessFinding(name="fdm_bed_contact", passed=ok,
                          detail=f"{contact:.0f} mm² on the bed, {share:.0%} of the footprint"
                                 + ("" if ok else f"; it may detach: the part stands on its lowest point "
                                    f"{_fmt(lowest)}: give it a flat base there, or a brim"))  # fmt: skip


# --------------------------------------------------------------------------- resin


def resin_checks(body: Body, rules: ResinProcess) -> list[ProcessFinding]:
    if body.mesh.is_empty:
        return []
    return [_walls(body, "resin_wall_thickness", rules.min_wall_mm), _resin_drains(body)]


def _resin_drains(body: Body) -> ProcessFinding:
    """A hollow sealed inside the part fills with resin that never drains or cures: each needs a drain hole.
    Such a hollow is a shell of the mesh facing inwards, so its volume is negative."""
    cavities = [shell for shell in body.mesh.split(only_watertight=False) if shell.is_watertight and shell.volume < 0]
    if not cavities:
        return ProcessFinding(name="resin_drains", passed=True, detail="no sealed hollow inside the part")
    largest = max(cavities, key=lambda shell: -shell.volume)
    return ProcessFinding(name="resin_drains", passed=False,
                          detail=f"{len(cavities)} sealed hollow{'s' if len(cavities) > 1 else ''} inside the part "
                                 f"(largest {-largest.volume:.0f} mm³ at {_fmt(largest.centroid)}) trap uncured resin: "
                                 "open each with a drain hole")  # fmt: skip


# --------------------------------------------------------------------------- corners


def _features(body: Body) -> Features:
    (leaf,) = body.leaves
    return leaf.part.features


def _unverified(name: str, why: Unmeasurable) -> ProcessFinding:
    return ProcessFinding(name=name, passed=None, detail=str(why))


def _vertical(corner: InsideCorner, tol_deg: float) -> bool:
    return abs(corner.axis[2]) / float(np.linalg.norm(corner.axis)) >= math.cos(math.radians(tol_deg))


# --------------------------------------------------------------------------- CNC milling


def cnc_milling_checks(body: Body, rules: CncMillingProcess) -> list[ProcessFinding]:
    mesh, tol = body.mesh, body.settings.tolerances
    # A cutter from +Z cannot reach a surface facing down, flat or curved (the top of a horizontal hole).
    down = (mesh.face_normals[:, 2] < -math.sin(math.radians(tol.angle_deg))) & (
        mesh.triangles_center[:, 2] > mesh.bounds[0][2] + tol.position_mm
    )
    area = float(mesh.area_faces[down].sum())
    setups = ProcessFinding(name="cnc_setups", passed=area == 0,
                            detail="every face is reachable from +Z or is the bottom" if area == 0 else
                            f"{area:.0f} mm² of faces point down above the bottom: they need a second setup")  # fmt: skip
    try:
        corners = [c for c in _features(body).inside_corners() if _vertical(c, tol.angle_deg)]
    except Unmeasurable as e:
        return [_unverified("cnc_inside_corners", e), _unverified("cnc_pocket_depth", e), setups]
    tool_r = rules.tool_diameter_mm / 2
    sharp = [c for c in corners if c.radius == 0]
    small = [c for c in corners if 0 < c.radius < tool_r]
    reach = ProcessFinding(
        name="cnc_inside_corners", passed=not sharp and not small,
        detail="every vertical inside corner has at least the tool radius" if not sharp and not small else
        "; ".join(filter(None, [
            f"{len(sharp)} sharp vertical inside corners (first at {_fmt(sharp[0].at)})" if sharp else "",
            f"{len(small)} inside radii under the {tool_r:g} mm tool radius (smallest r={min(c.radius for c in small):.2f})" if small else "",
        ])) + f": a {rules.tool_diameter_mm:g} mm cutter cannot reach them",
    )  # fmt: skip
    deep = [c for c in corners if c.radius > 0 and c.extent > rules.max_depth_ratio * 2 * c.radius]
    depth = ProcessFinding(
        name="cnc_pocket_depth", passed=not deep,
        detail="no pocket deeper than its corner allows" if not deep else
        f"{len(deep)} pocket corners deeper than {rules.max_depth_ratio:g} × their diameter "
        f"(worst {max(c.extent / (2 * c.radius) for c in deep):.1f} ×)",
    )  # fmt: skip
    return [reach, depth, setups]


# --------------------------------------------------------------------------- sheet metal


def sheet_metal_checks(body: Body, rules: SheetMetalProcess) -> list[ProcessFinding]:
    _, thickness = wall_thickness(body.mesh, body.settings.wall_samples)
    measured = thickness[np.isfinite(thickness)]
    if not len(measured):
        walls = ProcessFinding(name="sheet_thickness", passed=None, detail="not measurable: the mesh is not closed")
    else:
        thinnest, median = float(measured.min()), float(np.median(measured))
        ok = all(abs(v - rules.thickness_mm) <= rules.thickness_tolerance_mm for v in (thinnest, median))
        walls = ProcessFinding(name="sheet_thickness", passed=ok,
                               detail=f"thinnest {thinnest:.2f} mm, median {median:.2f} mm (sheet "
                                      f"{rules.thickness_mm:g} ± {rules.thickness_tolerance_mm:g} mm)")  # fmt: skip
    try:
        corners = _features(body).inside_corners()
    except Unmeasurable as e:
        return [walls, _unverified("sheet_bend_radius", e)]
    sharp = [c for c in corners if c.radius == 0]
    tight = [c.radius for c in corners if 0 < c.radius < rules.min_bend_radius_mm]
    ok = not sharp and not tight
    bends = ProcessFinding(
        name="sheet_bend_radius", passed=ok,
        detail=f"every bend's inside radius is at least {rules.min_bend_radius_mm:g} mm" if ok else
        "; ".join(filter(None, [f"{len(sharp)} sharp inside bends" if sharp else "",
                                f"{len(tight)} bends tighter than {rules.min_bend_radius_mm:g} mm "
                                f"(tightest r={min(tight):.2f})" if tight else ""])),
    )  # fmt: skip
    return [walls, bends]


# --------------------------------------------------------------------------- injection molding


def injection_molding_checks(body: Body, rules: InjectionMoldingProcess) -> list[ProcessFinding]:
    mesh = body.mesh
    no_draft = np.abs(mesh.face_normals[:, 2]) < math.sin(math.radians(rules.min_draft_deg))
    area = float(mesh.area_faces[no_draft].sum())
    draft = ProcessFinding(name="molding_draft", passed=area == 0,
                           detail=f"every wall has at least {rules.min_draft_deg:g}° of draft along Z" if area == 0 else
                           f"{area:.0f} mm² of walls have under {rules.min_draft_deg:g}° of draft along Z: they "
                           "drag on the mold")  # fmt: skip
    _, thickness = wall_thickness(mesh, body.settings.wall_samples)
    measured = thickness[np.isfinite(thickness)]
    if not len(measured):
        return [
            draft,
            ProcessFinding(name="molding_walls", passed=None, detail="not measurable: the mesh is not closed"),
        ]
    ratio = float(measured.max() / measured.min())
    walls = ProcessFinding(name="molding_walls", passed=ratio <= rules.max_wall_ratio,
                           detail=f"walls from {measured.min():.2f} to {measured.max():.2f} mm ({ratio:.1f} ×, "
                                  f"limit {rules.max_wall_ratio:g} ×)" + ("" if ratio <= rules.max_wall_ratio else
                                  ": thick sections sink and warp"))  # fmt: skip
    return [draft, walls]
