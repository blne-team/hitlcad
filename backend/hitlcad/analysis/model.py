"""A built design in memory: its parts in their own frames, where `assembly.py` placed them, its
components' built designs, and the bodies requirement checks measure.

Every analysis works on this model; it is rebuilt from a build's outputs (its report and part files),
so a component built earlier is loaded the same way as one built now.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from hitlcad.analysis.mesh import load_mesh, measure, measure_union
from hitlcad.core import frames as fr
from hitlcad.core.address import End, InstancePath, Segment, is_interface_ref
from hitlcad.core.design import DesignSpec
from hitlcad.core.report import CatalogInfo, MeshMetrics, Reads
from hitlcad.core.structure import Measured, PartSpec, Tolerances, _Connector
from hitlcad.core.types import Value
from hitlcad.materials import Material

if TYPE_CHECKING:
    import manifold3d
    import trimesh


@dataclass(frozen=True)
class AnalysisSettings:
    tolerances: Tolerances
    overlap_mm3: float = field(metadata={"doc": "Bodies sharing less than this touch; they do not overlap"})
    mesh_tolerance_mm: float = field(metadata={"doc": "Chordal deviation of the meshes measured"})
    wall_samples: int = field(metadata={"doc": "Surface points probed for wall thickness"})


class StructureError(LookupError):
    """A name in design.json or assembly.py that does not resolve; reported by the structure analysis."""


@dataclass
class PartGeometry:
    """One part, built once, in its own frame."""

    name: str
    spec: PartSpec
    file: str
    backend: str
    mesh_file: Path
    step_file: Path | None
    objects_file: Path | None
    frames: dict[str, list[fr.Frame]]
    catalog: CatalogInfo | None
    reads: Reads
    cached: bool
    valid: bool | None = None

    @cached_property
    def mesh(self) -> trimesh.Trimesh:
        return load_mesh(self.mesh_file)

    @cached_property
    def metrics(self) -> MeshMetrics:
        """Measured on the mesh; a B-rep part's bodies are its solids."""
        metrics = measure(self.mesh)
        if self.shape is not None:
            metrics = metrics.model_copy(update={"bodies": len(self.shape.solids())})
        return metrics

    @cached_property
    def digest(self) -> str:
        """The part's mesh by content: what cached results about it depend on."""
        return hashlib.sha256(self.mesh_file.read_bytes()).hexdigest()

    @cached_property
    def shape(self) -> Any | None:
        """The B-rep, when the part's backend has one."""
        if self.step_file is None:
            return None
        from build123d import import_step

        return import_step(str(self.step_file))

    @cached_property
    def manifold(self) -> manifold3d.Manifold:
        import manifold3d

        solid = manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(self.mesh.vertices, dtype=np.float32),
                                                    tri_verts=np.asarray(self.mesh.faces, dtype=np.uint32)))  # fmt: skip
        if solid.status() != manifold3d.Error.NoError or solid.is_empty():
            raise ValueError(f"part {self.name} is not a closed mesh")
        return solid

    @property
    def material(self) -> str | None:
        """The provider's material for catalog geometry it made, else the one design.json gives."""
        return self.catalog.material if self.catalog else self.spec.material


@dataclass
class Leaf:
    """One placed instance of a part, in the frame of the design being analysed."""

    path: str
    part: PartGeometry
    owner: BuiltDesign
    transform: fr.Frame

    @cached_property
    def mesh(self) -> trimesh.Trimesh:
        return self.part.mesh.copy().apply_transform(self.transform)

    @cached_property
    def manifold(self) -> manifold3d.Manifold:
        return self.part.manifold.transform(self.transform[:3, :])

    @cached_property
    def bounds(self) -> np.ndarray:
        return self.mesh.bounds


@dataclass
class PlacedFrame:
    """One frame of a connector end, where the assembly put it."""

    instance: str
    part: PartGeometry
    owner: BuiltDesign
    connector: str
    spec: _Connector
    index: int
    frame: fr.Frame
    measured: Measured

    @property
    def address(self) -> str:
        return f"{self.instance}.{self.connector}"


@dataclass
class BuiltDesign:
    spec: DesignSpec
    folder: Path
    values: dict[str, Value]
    interfaces: dict[str, Any]
    interface_reads: dict[str, Reads]
    assembly_reads: Reads
    parts: dict[str, PartGeometry]
    placements: dict[str, list[fr.Frame]]
    components: dict[str, BuiltDesign]
    materials: dict[str, Material]
    tolerances: Tolerances
    measured: dict[tuple[str, str], list[Measured]] = field(default_factory=dict)

    # ------------------------------------------------------------------ values

    def nominal(self, ref: str) -> Any:
        if is_interface_ref(ref):
            name = ref.removeprefix("interfaces.")
            if name not in self.interfaces:
                raise StructureError(f"no interface value {name!r}")
            return self.interfaces[name]
        if ref not in self.values:
            raise StructureError(f"no parameter {ref!r}")
        return self.values[ref]

    def count(self, connector: _Connector) -> int:
        value = connector.count if isinstance(connector.count, int) else self.nominal(connector.count)
        return int(value)

    # ------------------------------------------------------------------ instances

    def instance_names(self, name: str) -> list[str]:
        """The addresses of the instances of one part or component: `lid` when placed once, else `planet[k]`."""
        placed = self.placements.get(name, [])
        return [name] if len(placed) == 1 else [f"{name}[{k}]" for k in range(len(placed))]

    def instances(self) -> Iterator[tuple[str, str, fr.Frame]]:
        """(address, part or component name, transform) of this design's own instances."""
        for name, placed in self.placements.items():
            for address, transform in zip(self.instance_names(name), placed, strict=True):
                yield address, name, transform

    def select(self, segment: Segment) -> list[tuple[str, fr.Frame]]:
        """The instances a segment names: all of a part or component, or one of them."""
        if segment.name not in self.placements:
            what = "placed by assembly.py" if segment.name in {*self.spec.parts, *self.spec.components} else "declared"
            raise StructureError(f"{segment.name!r} is not {what}")
        placed = self.placements[segment.name]
        names = self.instance_names(segment.name)
        if segment.index is None:
            return list(zip(names, placed, strict=True))
        if segment.index >= len(placed):
            raise StructureError(f"{segment.name} has {len(placed)} instances, not {segment.index + 1}")
        return [(names[segment.index], placed[segment.index])]

    def resolve(self, address: str) -> tuple[str, fr.Frame, str]:
        """One instance (possibly inside components): (canonical address, transform, part or component name)."""
        path = InstancePath.parse(address)
        design, transform, names = self, fr.IDENTITY, []
        for depth, segment in enumerate(path.segments):
            hits = design.select(segment)
            if len(hits) != 1:
                raise StructureError(
                    f"{address!r} names {len(hits)} instances; say which: {', '.join(h[0] for h in hits)}"
                )
            name, local = hits[0]
            names.append(name)
            transform = transform @ local
            if depth < len(path.segments) - 1:
                if segment.name not in design.components:
                    raise StructureError(f"{segment.name!r} is a part; only components have instances inside")
                design = design.components[segment.name]
        return "/".join(names), transform, path.leaf.name

    def leaves(self, prefix: str = "", transform: fr.Frame = fr.IDENTITY) -> list[Leaf]:
        """Every placed part instance, through components, in this design's frame."""
        out = []
        for address, name, local in self.instances():
            path, placed = f"{prefix}{address}", transform @ local
            if name in self.components:
                out += self.components[name].leaves(f"{path}/", placed)
            else:
                out.append(Leaf(path, self.parts[name], self, placed))
        return out

    def leaves_of(self, address: str) -> list[Leaf]:
        canonical, transform, name = self.resolve(address)
        owner = self._owner(address)
        if name in owner.components:
            return owner.components[name].leaves(f"{canonical}/", transform)
        return [Leaf(canonical, owner.parts[name], owner, transform)]

    def _owner(self, address: str) -> BuiltDesign:
        design = self
        for segment in InstancePath.parse(address).segments[:-1]:
            design = design.components[segment.name]
        return design

    # ------------------------------------------------------------------ connectors

    def frames(self, text: str) -> list[PlacedFrame]:
        """Every frame of a connector end, placed in this design's frame."""
        end = End.parse(text)
        name = end.owner.name
        if name in self.spec.components:
            if end.connector not in self.components[name].spec.exports:
                exports = ", ".join(self.components[name].spec.exports) or "none"
                raise StructureError(f"component {name} exports no connector {end.connector!r}; exports: {exports}")
            inner = self.components[name].frames(self.components[name].spec.exports[end.connector])
            out = []
            for address, local in self.select(end.owner):
                for pf in inner:
                    out.append(PlacedFrame(f"{address}/{pf.instance}", pf.part, pf.owner, pf.connector, pf.spec,
                                           pf.index, local @ pf.frame, pf.measured))  # fmt: skip
            return out
        if name not in self.spec.parts:
            raise StructureError(f"{name!r} is not a part or component of {self.spec.name}")
        part = self.parts[name]
        spec = part.spec.connectors.get(end.connector)
        if spec is None:
            known = ", ".join(part.spec.connectors) or "none"
            raise StructureError(f"part {name} declares no connector {end.connector!r}; its connectors: {known}")
        measured = self.measured.get((name, end.connector), [])
        out = []
        for address, local in self.select(end.owner):
            for k, frame in enumerate(part.frames[end.connector]):
                m = measured[k] if k < len(measured) else Measured(status="unverified", detail="not measured")
                out.append(PlacedFrame(address, part, self, end.connector, spec, k, local @ frame, m))
        return out

    def measure_connectors(self) -> None:
        """Measure every declared connector's frames on its part (before the analyses read them)."""
        for name, part in self.parts.items():
            for cname, spec in part.spec.connectors.items():
                try:
                    nominals = self.nominals(spec)
                except StructureError as e:
                    self.measured[(name, cname)] = [Measured(status="fail", detail=str(e))]
                    continue
                self.measured[(name, cname)] = [spec.measure(part.shape, f, self.tolerances, nominals)
                                                for f in part.frames.get(cname, [])]  # fmt: skip

    def nominals(self, spec: _Connector) -> dict[str, float]:
        out: dict[str, float] = {k: float(self.nominal(ref)) for k, ref in spec.nominal_refs().items()}
        if getattr(spec, "internal", False):
            out["internal"] = 1.0
        return out


# --------------------------------------------------------------------------- bodies for checks


class Body:
    """What a requirement or process check measures: placed part instances, as one body."""

    def __init__(self, name: str, leaves: list[Leaf], settings: AnalysisSettings) -> None:
        self.name = name
        self.leaves = leaves
        self.settings = settings

    @cached_property
    def mesh(self) -> trimesh.Trimesh:
        import trimesh

        return trimesh.util.concatenate([leaf.mesh for leaf in self.leaves])

    @cached_property
    def metrics(self) -> MeshMetrics:
        if len(self.leaves) == 1:
            return measure(self.mesh)
        return measure_union(measure(self.mesh), [leaf.part.metrics for leaf in self.leaves])

    def holes(self) -> list[Any] | None:
        from hitlcad.analysis.geometry import find_holes

        if any(leaf.part.shape is None for leaf in self.leaves):
            return None
        return [hole for leaf in self.leaves for hole in find_holes(leaf.part.step_file)]  # type: ignore[arg-type]

    def thinnest_wall(self) -> float | None:
        from hitlcad.analysis.processes import wall_thickness

        _, thickness = wall_thickness(self.mesh, self.settings.wall_samples)
        measured = thickness[np.isfinite(thickness)]
        return float(measured.min()) if len(measured) else None

    def mass_g(self) -> tuple[float | None, str]:
        """(grams, '') or (None, why it is unknown)."""
        total, unknown = 0.0, []
        for leaf in self.leaves:
            name = leaf.part.material
            volume = leaf.part.metrics.volume
            if name is None:
                unknown.append(f"{leaf.path} has no material")
            elif name not in leaf.owner.materials:
                unknown.append(f"{leaf.path}: material {name!r} is not in the material registry")
            elif volume is None:
                unknown.append(f"{leaf.path} is not a closed mesh")
            else:
                total += volume * leaf.owner.materials[name].density_g_cm3 / 1000
        return (None, "; ".join(unknown)) if unknown else (total, "")

    @cached_property
    def manifold(self) -> manifold3d.Manifold:
        import manifold3d

        return manifold3d.Manifold.batch_boolean([leaf.manifold for leaf in self.leaves], manifold3d.OpType.Add)

    def overlap(self, other: Body) -> float:
        return float((self.manifold ^ other.manifold).volume())

    def gap(self, other: Body) -> float:
        """Smallest distance (mm) between the two; 0 when they touch or overlap."""
        lo = np.minimum(self.mesh.bounds[0], other.mesh.bounds[0])
        hi = np.maximum(self.mesh.bounds[1], other.mesh.bounds[1])
        span = float(np.linalg.norm(hi - lo))
        search = 1.0  # mm; widened ×8 until the gap is found: fitted parts are close, so near first is cheap
        while (gap := float(self.manifold.min_gap(other.manifold, search))) >= search and search < span:
            search = min(search * 8, span)
        return gap


class BuildBodies:
    """The bodies of a built design, by address, for requirement checks."""

    def __init__(self, built: BuiltDesign, settings: AnalysisSettings) -> None:
        self.built = built
        self.settings = settings

    @property
    def overlap_mm3(self) -> float:
        return self.settings.overlap_mm3

    def _body(self, name: str, leaves: list[Leaf]) -> Body:
        return Body(name, leaves, self.settings)

    def whole(self) -> Body:
        return self._body(self.built.spec.name, self.built.leaves())

    def part(self, name: str) -> Body:
        """A part on its own, in its own frame (the way it is made)."""
        return self._body(name, [Leaf(name, self.built.parts[name], self.built, fr.IDENTITY)])

    def subject(self, target: str | None) -> Body:
        if target is None:
            return self.whole()
        if target in self.built.parts:
            return self.part(target)
        return self.placed(target)

    def placed(self, address: str) -> Body:
        return self._body(address, self.built.leaves_of(address))
