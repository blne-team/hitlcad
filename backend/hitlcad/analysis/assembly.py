"""The bodies a build is measured on: the whole design and, for an assembly (build(p) returns
{part name: shape}), each part on its own mesh (part-<name>.stl, with part-<name>.step on B-rep backends).
Between parts, manifold3d gives exact overlap volumes and the minimum gap, on closed meshes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from itertools import combinations
from pathlib import Path

import manifold3d
import numpy as np
import trimesh

from hitlcad.analysis.geometry import Hole, find_holes
from hitlcad.analysis.printability import wall_thickness
from hitlcad.core.types import OVERLAP_MM3, Check, MeshMetrics, PartInfo

PART_PREFIX = "part-"
GAP_SEARCH_MM = 1.0  # first search radius for the gap between two parts


@dataclass
class Part:
    name: str
    mesh: trimesh.Trimesh
    metrics: MeshMetrics
    step: Path | None

    def info(self) -> PartInfo:
        m = self.metrics
        return PartInfo(name=self.name, bbox_min=m.bbox_min, bbox_max=m.bbox_max, size=m.size, volume=m.volume,
                        bodies=m.bodies)  # fmt: skip

    def holes(self) -> list[Hole]:
        """Round holes of the B-rep; only parts with a STEP have them."""
        assert self.step is not None
        return find_holes(self.step)

    def thinnest_wall(self) -> float | None:
        """mm; None when no wall can be measured (an open mesh)."""
        _, thickness = wall_thickness(self.mesh)
        measured = thickness[np.isfinite(thickness)]
        return float(measured.min()) if len(measured) else None

    @cached_property
    def _solid(self) -> manifold3d.Manifold:
        solid = manifold3d.Manifold(
            manifold3d.Mesh(
                vert_properties=np.asarray(self.mesh.vertices, dtype=np.float32),
                tri_verts=np.asarray(self.mesh.faces, dtype=np.uint32),
            )
        )
        if solid.status() != manifold3d.Error.NoError or solid.is_empty():
            raise ValueError(f"part {self.name} is not a closed mesh")
        return solid

    def overlap(self, other: Part) -> float:
        """Volume (mm³) the two parts share."""
        return float((self._solid ^ other._solid).volume())

    def gap(self, other: Part) -> float:
        """Smallest distance (mm) between the two parts; 0 when they touch or overlap."""
        lo = np.minimum(self.metrics.bbox_min, other.metrics.bbox_min)
        hi = np.maximum(self.metrics.bbox_max, other.metrics.bbox_max)
        span = float(np.linalg.norm(hi - lo))
        # min_gap's cost grows with how far it searches, and fitted parts are close: search near first, then widen.
        search = GAP_SEARCH_MM
        while (gap := float(self._solid.min_gap(other._solid, search))) >= search and search < span:
            search = min(search * 8, span)
        return gap


@dataclass
class Build:
    """What a build's checks and requirements are measured on: the whole design and its named parts."""

    whole: Part
    parts: dict[str, Part]

    def subject(self, name: str | None) -> Part:
        """The part a check names, or the whole design."""
        if name is None:
            return self.whole
        if name not in self.parts:
            known = f"parts: {', '.join(self.parts)}" if self.parts else "the design is not an assembly"
            raise LookupError(f"no part named {name!r} ({known})")
        return self.parts[name]


def part_name(file_name: str) -> str:
    """'part-lid.stl' -> 'lid'."""
    return Path(file_name).stem.removeprefix(PART_PREFIX)


def interference(parts: list[Part]) -> list[Check]:
    """One warning per pair of parts that overlap; a single pass when none do."""
    if len(parts) < 2:
        return []
    found = []
    for a, b in combinations(parts, 2):
        try:
            shared = a.overlap(b)
        except ValueError as e:
            return [Check(name="interference", passed=False, severity="warning", detail=f"overlaps not checked: {e}")]
        if shared > OVERLAP_MM3:
            found.append(Check(name=f"interference: {a.name} / {b.name}", passed=False, severity="warning",
                               detail=f"{a.name} and {b.name} overlap by {shared:.1f} mm³"))  # fmt: skip
    return found or [
        Check(name="interference", passed=True, severity="warning", detail=f"none of the {len(parts)} parts overlap")
    ]
