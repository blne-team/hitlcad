"""Backend-independent inspection of the canonical STL (mm, Z up)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from hitlcad.analysis.assembly import Build, interference
from hitlcad.core.types import BuildSpec, Check, MeshMetrics


def load_mesh(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh", process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{path.name} did not load as a triangle mesh")
    return mesh


def measure(mesh: trimesh.Trimesh) -> MeshMetrics:
    if mesh.is_empty:
        zero = (0.0, 0.0, 0.0)
        return MeshMetrics(bbox_min=zero, bbox_max=zero, size=zero, volume=None, surface_area=0.0,
                           triangles=0, bodies=0, watertight=False, winding_consistent=False)  # fmt: skip
    lo, hi = (tuple(round(float(v), 4) for v in row) for row in mesh.bounds)
    size = tuple(round(float(v), 4) for v in np.asarray(hi) - np.asarray(lo))
    bodies = len(mesh.split(only_watertight=False))
    watertight = bool(mesh.is_watertight)
    return MeshMetrics(
        bbox_min=lo,  # type: ignore[arg-type]
        bbox_max=hi,  # type: ignore[arg-type]
        size=size,  # type: ignore[arg-type]
        volume=round(float(abs(mesh.volume)), 4) if watertight else None,
        surface_area=round(float(mesh.area), 4),
        triangles=len(mesh.faces),
        bodies=bodies,
        watertight=watertight,
        winding_consistent=bool(mesh.is_winding_consistent),
    )


def measure_assembly(whole: MeshMetrics, parts: list[MeshMetrics]) -> MeshMetrics:
    """The whole of an assembly: closed when each part is, its material the parts' added up. Parts that
    touch share faces in the combined mesh, which on its own would read as open."""
    closed = all(p.watertight for p in parts)
    return whole.model_copy(update={
        "watertight": closed,
        "winding_consistent": all(p.winding_consistent for p in parts),
        "volume": round(sum(p.volume or 0.0 for p in parts), 4) if closed else None,
    })  # fmt: skip


@dataclass
class CheckContext:
    spec: BuildSpec
    build: Build
    info: dict[str, Any]  # what the backend reported, e.g. brep_valid


CheckFn = Callable[[CheckContext], list[Check]]


def _geometry(ctx: CheckContext) -> list[Check]:
    m = ctx.build.whole.metrics
    out = [Check(name="non_empty", passed=m.triangles > 0, detail=f"{m.triangles} triangles")]
    if m.triangles == 0:
        return out
    out.append(Check(name="watertight", passed=m.watertight, severity="warning",
                     detail="closed manifold" if m.watertight else "mesh has open edges; not printable as-is"))  # fmt: skip
    out.append(Check(name="consistent_winding", passed=m.winding_consistent, severity="warning",
                     detail="face orientation consistent" if m.winding_consistent else "some faces are flipped"))  # fmt: skip
    out.append(Check(name="single_body", passed=m.bodies == 1, severity="info", detail=f"{m.bodies} connected bodies"))
    largest = max(m.size)
    out.append(Check(name="plausible_size", passed=0.1 <= largest <= 10_000, severity="warning",
                     detail=f"largest extent {largest:.2f} mm"))  # fmt: skip
    out.append(Check(name="rests_on_build_plane", passed=abs(m.bbox_min[2]) <= 0.01, severity="info",
                     detail=f"lowest Z = {m.bbox_min[2]:.3f} mm"))  # fmt: skip
    return out


def _brep(ctx: CheckContext) -> list[Check]:
    if "brep_valid" not in ctx.info:
        return []
    return [Check(name="brep_valid", passed=bool(ctx.info["brep_valid"]),
                  detail="OpenCASCADE shape check " + ("passed" if ctx.info["brep_valid"] else "failed"))]  # fmt: skip


def _assembly(ctx: CheckContext) -> list[Check]:
    return interference(list(ctx.build.parts.values()))


def _manufacturing(ctx: CheckContext) -> list[Check]:
    """The checks of the process design.json declares, on the whole design or on each assembly part."""
    process = ctx.spec.manufacturing
    if process is None:
        return []
    if not ctx.build.parts:
        return process.checks(ctx.build.whole)
    return [check.model_copy(update={"name": f"{check.name}: {part.name}"})
            for part in ctx.build.parts.values() for check in process.checks(part)]  # fmt: skip


CHECKS: list[CheckFn] = [_geometry, _brep, _assembly, _manufacturing]


def run_checks(ctx: CheckContext) -> list[Check]:
    return [check for fn in CHECKS for check in fn(ctx)]
