"""A built design in memory: its parts in their own frames, where `assembly.py` placed them, its
components' built designs, and the bodies requirement checks measure.

Every analysis works on this model; it is rebuilt from a build's outputs (its report and part files),
so a component built earlier is loaded the same way as one built now.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from hitlcad.core import frames as fr
from hitlcad.core.address import End, InstancePath, Segment, is_interface_ref
from hitlcad.core.design import DesignSpec, PartSpec
from hitlcad.core.report import CatalogInfo, MassProperties, MeshMetrics, Reads
from hitlcad.core.structure import Connector, Measured, Tolerances
from hitlcad.core.types import Value
from hitlcad.geometry import cadview
from hitlcad.geometry.features import BrepFeatures, Features, Hole, MeshFeatures
from hitlcad.geometry.mesh import closed, load_mesh, measure, measure_union
from hitlcad.kernel.errors import StructureError, Unmeasurable
from hitlcad.materials import Material

if TYPE_CHECKING:
    import manifold3d
    import trimesh

    from hitlcad.analysis.base import ResultCache
    from hitlcad.core.loops import Solver
    from hitlcad.geometry.walls import Walls


@dataclass(frozen=True)
class AnalysisSettings:
    overlap_mm3: float = field(metadata={"doc": "Bodies sharing less than this touch; they do not overlap"})
    mesh_tolerance_mm: float = field(metadata={"doc": "Chordal deviation of the meshes measured"})
    wall_samples: int = field(metadata={"doc": "Surface points probed for wall thickness"})
    solver: str = field(default="", metadata={"doc": "The loop solver's name (`Settings.solver`)"})
    loop_solver: Solver | None = field(default=None, metadata={"doc": "That solver, None when no plugin gives it"})


@dataclass
class PartGeometry:
    """One part, built once, in its own frame."""

    name: str
    spec: PartSpec
    file: str
    backend: str
    mesh_file: Path
    step_file: Path | None
    objects_file: Path
    frames: dict[str, list[fr.Frame]]
    catalog: CatalogInfo | None
    reads: Reads
    cached: bool
    valid: bool | None = None

    @cached_property
    def mesh(self) -> trimesh.Trimesh:
        return load_mesh(self.mesh_file)

    @cached_property
    def solid(self) -> manifold3d.Manifold | None:
        """The solid its mesh bounds, or None when the mesh is not closed (`mesh.closed`)."""
        return closed(self.mesh)

    @cached_property
    def metrics(self) -> MeshMetrics:
        """Measured on the mesh; its bodies as its representation counts them (a B-rep's solids)."""
        return measure(self.mesh, self.solid, self.features.bodies())

    @cached_property
    def features(self) -> Features:
        """Its geometry, measured on the B-rep when its backend made one, else on the mesh."""

        def objects() -> list[cadview.ObjectMesh]:
            return cadview.object_meshes(cadview.load_mesh(self.objects_file))

        if self.step_file is None:
            return MeshFeatures(self.name, lambda: self.mesh, objects)
        return BrepFeatures(self.name, cadview.load_step(self.step_file), lambda: self.mesh, objects)

    @cached_property
    def digest(self) -> str:
        """The part's mesh by content: what cached results about it depend on."""
        return hashlib.sha256(self.mesh_file.read_bytes()).hexdigest()

    @property
    def manifold(self) -> manifold3d.Manifold:
        if self.solid is None:
            raise Unmeasurable(f"part {self.name} is not a closed mesh")
        return self.solid

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
    def vertices(self) -> np.ndarray:
        return self.part.mesh.vertices @ self.transform[:3, :3].T + self.transform[:3, 3]

    @cached_property
    def bounds(self) -> np.ndarray:
        return np.array([self.vertices.min(axis=0), self.vertices.max(axis=0)])


@dataclass
class PlacedFrame:
    """One frame of a connector end, where the assembly put it."""

    instance: str
    part: PartGeometry
    owner: BuiltDesign
    connector: str
    spec: Connector
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

    def count(self, connector: Connector) -> int:
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
        try:
            path = InstancePath.parse(address)
        except ValueError as e:
            raise StructureError(str(e)) from None
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

    @cached_property
    def mesh(self) -> trimesh.Trimesh:
        """Every placed part instance as one mesh, in this design's frame."""
        import trimesh

        leaves = self.leaves()
        return trimesh.util.concatenate([leaf.mesh for leaf in leaves]) if leaves else trimesh.Trimesh()

    def mass(self, part: PartGeometry, transform: fr.Frame = fr.IDENTITY) -> MassProperties | None:
        """One of its parts' mass, centre and inertia about the centre, placed by `transform`: known when the
        part's material is and its mesh is closed (`unknown_mass` says why it is not)."""
        if self.unknown_mass(part):
            return None
        props = part.mesh.mass_properties  # at unit density
        density = self.materials[part.material].density_g_cm3 / 1000  # type: ignore[index]  # g/mm³
        r = transform[:3, :3]
        return MassProperties(
            mass_g=float(props["mass"]) * density,
            centre_mm=tuple(float(v) for v in (transform @ np.append(props["center_mass"], 1.0))[:3]),
            inertia_g_mm2=tuple(tuple(float(v) for v in row) for row in r @ props["inertia"] @ r.T * density),
        )  # type: ignore[arg-type]

    def unknown_mass(self, part: PartGeometry) -> str:
        """Why a part's mass is unknown, or ''."""
        if part.material is None:
            return "has no material"
        if part.material not in self.materials:
            return f"has material {part.material!r}, which is not in the material registry"
        return "" if part.metrics.watertight else "is not a closed mesh"

    @cached_property
    def motion_plans(self) -> list[Any]:
        """The motion analysis's plans (`analysis.motion.plans`), shared by the analysis and the report."""
        from hitlcad.analysis.motion.plans import plans

        return plans(self)

    # ------------------------------------------------------------------ connectors

    def frames(self, text: str) -> list[PlacedFrame]:
        """Every frame of a connector end, placed in this design's frame."""
        try:
            end = End.parse(text)
        except ValueError as e:
            raise StructureError(str(e)) from None
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
        """Measure every declared connector's frames on its part (before the analyses read them). A nominal value
        that does not resolve fails every frame, so nothing reads the nominals that are not there."""
        for name, part in self.parts.items():
            for cname, spec in part.spec.connectors.items():
                frames = part.frames.get(cname, [])
                try:
                    nominals = self.nominals(spec)
                except StructureError as e:
                    self.measured[(name, cname)] = [Measured(status="fail", detail=str(e)) for _ in frames]
                    continue
                self.measured[(name, cname)] = [self._measure(spec, part, f, nominals) for f in frames]

    def _measure(self, spec: Connector, part: PartGeometry, frame: fr.Frame, nominals: dict[str, float]) -> Measured:
        try:
            return spec.measure(part.features, frame, self.tolerances, nominals)
        except Unmeasurable as e:
            return Measured(status="unverified", detail=str(e))
        except Exception as e:  # the connector kind's own code: unmeasured, and the build goes on
            from hitlcad.kernel.plugins import registry

            logging.getLogger(__name__).exception("measuring %s failed", spec.kind)
            found = registry()
            return Measured(
                status="unverified", detail=found.failed(found.origin(spec), "measuring it failed", e).message
            )

    def nominals(self, spec: Connector) -> dict[str, float]:
        return {k: float(self.nominal(ref)) for k, ref in spec.nominal_refs().items()} | spec.fixed_nominals()


# --------------------------------------------------------------------------- bodies for checks


#: A gap between solids is looked for this far (mm) first, and WIDEN times as far each time nothing is within
#: reach: fitted parts are close, so looking near first is cheap.
SEARCH_MM, WIDEN = 1.0, 4


def gap(a: manifold3d.Manifold, b: manifold3d.Manifold, upto: float) -> float:
    """Smallest distance (mm) between two solids, or `upto` when they are further apart; 0 when they touch
    or overlap."""
    search = SEARCH_MM
    while (found := float(a.min_gap(b, search))) >= search and search < upto:
        search = min(search * WIDEN, upto)
    return found


class Body:
    """What a requirement or process check measures: placed part instances, as one body."""

    def __init__(self, name: str, leaves: list[Leaf], settings: AnalysisSettings, tolerances: Tolerances) -> None:
        self.name = name
        self.leaves = leaves
        self.settings = settings
        self.tolerances = tolerances  # the design's own

    @cached_property
    def mesh(self) -> trimesh.Trimesh:
        import trimesh

        return trimesh.util.concatenate([leaf.mesh for leaf in self.leaves])

    @cached_property
    def bounds(self) -> np.ndarray:
        boxes = np.array([leaf.bounds for leaf in self.leaves])
        return np.array([boxes[:, 0].min(axis=0), boxes[:, 1].max(axis=0)])

    @cached_property
    def metrics(self) -> MeshMetrics:
        return measure_union(self.bounds, [leaf.part.metrics for leaf in self.leaves])

    def holes(self) -> list[Hole]:
        return [hole for leaf in self.leaves for hole in leaf.part.features.holes()]

    @cached_property
    def walls(self) -> Walls:
        """Its wall thickness; `Unmeasurable` where a part of it is not a closed mesh (`mesh.closed`): an open
        surface has no inside to measure across."""
        from hitlcad.geometry.walls import wall_thickness

        if not self.metrics.watertight:
            raise Unmeasurable(f"{self.name} is not a closed mesh: its walls cannot be measured")
        return wall_thickness(self.mesh, self.settings.wall_samples)

    def mass_g(self) -> tuple[float | None, str]:
        """(grams, '') or (None, why it is unknown)."""
        unknown = [f"{leaf.path} {why}" for leaf in self.leaves if (why := leaf.owner.unknown_mass(leaf.part))]
        if unknown:
            return None, "; ".join(unknown)
        return sum(leaf.owner.mass(leaf.part).mass_g for leaf in self.leaves), ""  # type: ignore[union-attr]

    @cached_property
    def manifold(self) -> manifold3d.Manifold:
        import manifold3d

        return manifold3d.Manifold.batch_boolean([leaf.manifold for leaf in self.leaves], manifold3d.OpType.Add)

    def overlap(self, other: Body) -> float:
        return float((self.manifold ^ other.manifold).volume())

    def gap(self, other: Body) -> float:
        """Smallest distance (mm) between the two; 0 when they touch or overlap."""
        span = np.maximum(self.bounds[1], other.bounds[1]) - np.minimum(self.bounds[0], other.bounds[0])
        return gap(self.manifold, other.manifold, float(np.linalg.norm(span)))


@dataclass(frozen=True)
class Moved:
    """The least gap a mate's motion brings two bodies to."""

    gap_mm: float
    where: str  # "at 30.0° of lid.hinge+base.pin"
    complete: bool  # the sweep reached the ends of every range
    mate: str


class BuildBodies:
    """The bodies of a built design, by address, for requirement checks."""

    def __init__(self, built: BuiltDesign, settings: AnalysisSettings, cache: ResultCache) -> None:
        self.built = built
        self.settings = settings
        self.cache = cache
        self._bodies: dict[tuple[str, str], Body] = {}

    @property
    def overlap_mm3(self) -> float:
        return self.settings.overlap_mm3

    def number(self, value: float | str | None) -> float | None:
        """A requirement's number: as written, or the parameter or interface value it names."""
        return value if value is None or not isinstance(value, str) else float(self.built.nominal(value))

    def _body(self, kind: str, name: str, leaves: Callable[[], list[Leaf]]) -> Body:
        """One body per subject, so what is measured on it (its walls, a union) is measured once per build."""
        if (kind, name) not in self._bodies:
            self._bodies[kind, name] = Body(name, leaves(), self.settings, self.built.tolerances)
        return self._bodies[kind, name]

    def whole(self) -> Body:
        return self._body("whole", self.built.spec.name, self.built.leaves)

    def part(self, name: str) -> Body:
        """A part on its own, in its own frame (the way it is made)."""
        return self._body("part", name, lambda: [Leaf(name, self.built.parts[name], self.built, fr.IDENTITY)])

    def subject(self, target: str | None) -> Body:
        if target is None:
            return self.whole()
        if target in self.built.parts:
            return self.part(target)
        return self.placed(target)

    def placed(self, address: str) -> Body:
        return self._body("placed", address, lambda: self.built.leaves_of(address))

    def moved_gap(self, a: Body, b: Body) -> Moved | None:
        """The least gap between two bodies while a mate moves one against the other, as motion analysis
        sweeps it. None when no mate does."""
        from hitlcad.analysis.motion import least_gaps

        pairs = {frozenset((p.path, q.path)) for p in a.leaves for q in b.leaves}
        found: Moved | None = None
        for mate, least in least_gaps(self.built, self.settings, self.cache):
            if frozenset((least.a, least.b)) not in pairs or found and found.gap_mm <= least.gap_mm:
                continue
            unit = "°" if least.kind == "angle" else " mm"
            at = "in a full turn" if least.at is None else f"at {least.at:.1f}{unit}"
            found = Moved(least.gap_mm, f"{at} of {mate}", least.complete, mate)
        return found
