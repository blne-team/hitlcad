"""Manufacturability checks for the process a design declares (design.json "manufacturing").

FDM: the part prints the way the model stands, +Z up on the bed at its lowest Z; each part of an
assembly is checked on its own, as it stands in the assembly, on the bed at its own lowest Z. The checks
measure the build's mesh (mm): wall thickness against the nozzle and the minimum wall, overhangs
steeper than the support angle, and how much of the footprint touches the bed. They are warnings:
a design that is hard to print still builds, and the agent and the human see why.
"""

from __future__ import annotations

import math

import numpy as np
import trimesh

from hitlcad.core.types import Check, FdmProcess

SAMPLES = 4000  # surface points probed for wall thickness, spread evenly over the area
ON_BED_MM = 0.05  # faces this close to the lowest Z rest on the bed
SMALL_AREA_MM2 = 1.0  # overhangs smaller than this print fine (bridging, chamfer tips)
MIN_BED_SHARE = 0.10  # of the footprint's bounding box: less than this risks the part detaching
WALL_EXIT_DEG = 60  # a wall's far side faces back within this angle; wedges sharper than it still count as walls


def _fmt(p: np.ndarray) -> str:
    return "(" + ", ".join(f"{c:.1f}" for c in p) + ")"


def wall_thickness(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """Points spread evenly over the surface and the wall thickness straight through the part at each (inf: not measured).

    A ray that leaves through a face not turned back towards it crossed a corner, not a wall: next to an edge it
    would read as thin as the distance to that edge, so that point is not measured."""
    points, faces = trimesh.sample.sample_surface_even(mesh, SAMPLES, seed=0)
    inward = -mesh.face_normals[faces]
    hit, ray, location = mesh.ray.intersects_id(points, inward, return_locations=True, multiple_hits=True)
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


def _wall_thickness(mesh: trimesh.Trimesh, rules: FdmProcess) -> Check:
    points, thickness = wall_thickness(mesh)
    measured = np.isfinite(thickness)
    if not measured.any():
        return Check(name="fdm_wall_thickness", passed=False, severity="warning",
                     detail="no wall thickness could be measured: the mesh is not closed")  # fmt: skip
    thinnest = int(np.argmin(np.where(measured, thickness, np.inf)))
    value = float(thickness[thinnest])
    thin_share = float(np.mean(thickness[measured] < rules.min_wall_mm))
    if value >= rules.min_wall_mm:
        return Check(name="fdm_wall_thickness", passed=True, severity="warning",
                     detail=f"thinnest wall {value:.2f} mm (minimum {rules.min_wall_mm:g} mm)")  # fmt: skip
    worse = f", thinner than the {rules.nozzle_mm:g} mm nozzle: it will not print" if value < rules.nozzle_mm else ""
    share = f"{thin_share:.0%}" if thin_share >= 0.01 else "under 1%"
    return Check(name="fdm_wall_thickness", passed=False, severity="warning",
                 detail=f"thinnest wall {value:.2f} mm at {_fmt(points[thinnest])}{worse}; "
                        f"{share} of the surface is under the {rules.min_wall_mm:g} mm minimum")  # fmt: skip


def _overhangs(mesh: trimesh.Trimesh, rules: FdmProcess) -> Check:
    # A face needs support when it is tilted further from vertical than the limit while facing down.
    steep = mesh.face_normals[:, 2] < -math.sin(math.radians(rules.max_overhang_deg))
    on_bed = mesh.triangles[:, :, 2].max(axis=1) <= mesh.bounds[0][2] + ON_BED_MM
    needs_support = steep & ~on_bed
    area = float(mesh.area_faces[needs_support].sum())
    if area < SMALL_AREA_MM2:
        return Check(name="fdm_overhangs", passed=True, severity="warning",
                     detail=f"no overhang steeper than {rules.max_overhang_deg:g}° from vertical")  # fmt: skip
    lowest = mesh.triangles_center[needs_support][np.argmin(mesh.triangles_center[needs_support][:, 2])]
    return Check(name="fdm_overhangs", passed=False, severity="warning",
                 detail=f"{area:.0f} mm² overhangs more than {rules.max_overhang_deg:g}° from vertical and needs "
                        f"support (lowest at {_fmt(lowest)}); chamfer it, reorient the part or split it")  # fmt: skip


def _bed_contact(mesh: trimesh.Trimesh) -> Check:
    flat_down = mesh.face_normals[:, 2] < -0.999
    on_bed = mesh.triangles[:, :, 2].max(axis=1) <= mesh.bounds[0][2] + ON_BED_MM
    contact = float(mesh.area_faces[flat_down & on_bed].sum())
    (x0, y0, _), (x1, y1, _) = mesh.bounds
    footprint = (x1 - x0) * (y1 - y0)
    share = contact / footprint if footprint > 0 else 0.0
    return Check(name="fdm_bed_contact", passed=share >= MIN_BED_SHARE, severity="warning",
                 detail=f"{contact:.0f} mm² on the bed, {share:.0%} of the footprint"
                        + ("" if share >= MIN_BED_SHARE else "; it may detach: add a flat base or a brim"))  # fmt: skip


def fdm_checks(mesh: trimesh.Trimesh, rules: FdmProcess) -> list[Check]:
    """Printability of one body on an FDM printer."""
    if mesh.is_empty:
        return []
    return [_wall_thickness(mesh, rules), _overhangs(mesh, rules), _bed_contact(mesh)]
