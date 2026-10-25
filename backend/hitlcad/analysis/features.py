"""What hitlcad asks of a part's shape, in its own frame, whatever the part is made of.

Everything that reads a part's geometry asks its `Features`, a built part's and a loose model file's
alike; nothing else looks at what a part is made of. Two adapters answer: `BrepFeatures` on its B-rep
faces, exactly, and `MeshFeatures` on its triangle mesh, to within the mesh's facets, which each
measurement reports as its uncertainty. Both know the part's mesh (what bodies, walls and overlaps are
measured on) and its named objects in their colours (what the view, 3MF and pick names show). A frame
with no matching feature fails, so a frame cannot drift away from the geometry it describes. What a
representation cannot answer at all (a mesh has no fillet radii and no faces, edges or vertices to
number) raises `Unmeasurable`, saying why.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from hitlcad.analysis import cadview
from hitlcad.analysis.cadview import ObjectMesh
from hitlcad.core import frames as fr
from hitlcad.core.structure import Measured, Tolerances

if TYPE_CHECKING:
    from collections.abc import Callable

    import trimesh

FULL_CIRCLE = 0.95  # of 2π: a cylindrical cut that goes this far round is a hole, not a fillet or slot end
#: Of the circle, the share a connector's hole or shaft must go round: a slot or a teardrop's point may open its
#: wall, a half-round notch at an edge is not a hole.
ON_CIRCLE = 0.6
#: B-rep surfaces of revolution: coaxial with a frame, they lead from its origin to its feature (a countersink's
#: cone, a chamfer or a fillet at the edge).
REVOLVED = frozenset({"CYLINDER", "CONE", "TORUS", "SPHERE", "REVOLUTION"})
#: Where across its face width a gear is measured, as shares of the width.
GEAR_SECTIONS = (0.1, 0.5, 0.9)
#: A gear's tip diameter agrees with m(z ± 2) to this share of its module: its teeth were cut for that module and
#: count, not merely near the pitch circle.
TIP_SHARE = 0.1


class Unmeasurable(ValueError):
    """What a part's representation cannot answer; the message says why."""


@dataclass(frozen=True)
class Hole:
    diameter: float
    depth: float


@dataclass(frozen=True)
class InsideCorner:
    """A concave corner between faces, where a cutter or a bend leaves its radius: sharp (radius 0) or
    rounded, running along `axis` for `extent` mm."""

    radius: float
    axis: tuple[float, float, float]
    extent: float
    at: tuple[float, float, float]


@dataclass(frozen=True)
class Piece:
    """What of a part has one colour: one shape in the view, one object in 3MF."""

    name: str
    color: str | None
    mesh: trimesh.Trimesh


class Features(ABC):
    """A part's geometry, in its own frame. `mesh` and `objects` load what it is measured on and what it
    shows, when first asked."""

    representation: ClassVar[str]

    def __init__(self, name: str, mesh: Callable[[], trimesh.Trimesh], objects: Callable[[], list[ObjectMesh]]) -> None:
        self.name = name
        self._load_mesh, self._load_objects = mesh, objects

    @cached_property
    def mesh(self) -> trimesh.Trimesh:
        return self._load_mesh()

    @cached_property
    def objects(self) -> list[ObjectMesh]:
        """Its named objects' meshes, in their colours."""
        return self._load_objects()

    @cached_property
    def pieces(self) -> list[Piece]:
        """The part by colour, each piece named by its materials."""
        by_color: dict[str | None, list[ObjectMesh]] = {}
        for o in self.objects:
            by_color.setdefault(o.color, []).append(o)
        names = {
            color: "+".join(dict.fromkeys(o.material for o in meshes if o.material))
            for color, meshes in by_color.items()
        }
        return [Piece(names[color] or self.name, color, cadview.geometry(meshes)) for color, meshes in by_color.items()]

    def named_at(self, point: Any) -> str | None:
        """The object nearest a point in the part's frame, where the part has more than one."""
        names = list(dict.fromkeys(o.name for o in self.objects))
        if len(names) < 2:
            return None
        gaps = [float(np.min(np.linalg.norm(o.mesh.vertices - np.asarray(point), axis=1))) for o in self.objects]
        return self.objects[int(np.argmin(gaps))].name

    @abstractmethod
    def view(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Tessellated for the 3D view: a shape and its entry template per thing the view draws, each entry's
        `loc` placing its shape in the part's frame."""

    @abstractmethod
    def bounds(self) -> np.ndarray:
        """(min, max) corners in the part's frame."""

    @abstractmethod
    def round(self, frame: fr.Frame, tol: Tolerances, *, inward: bool) -> Measured:
        """The hole (inward) or shaft coaxial with the frame, adjacent to its origin: a hole lies below the
        origin (it opens along +Z), a shaft above it (it runs from its shoulder along +Z). Of features that
        start equally close, a hole is the innermost (the space at the axis) and a shaft the outermost (what
        meets the hole), e.g. a bearing's bore, not the gap between its races."""

    @abstractmethod
    def face(self, frame: fr.Frame, tol: Tolerances) -> Measured:
        """The planar face whose outward normal is the frame's Z, with the origin on its plane and inside its
        outline (a mate connector may sit at the centre of a rim or an annulus, where there is no material)."""

    def gear(
        self, frame: fr.Frame, tol: Tolerances, *, module: float, teeth: int, width: float, internal: bool
    ) -> Measured:
        """The spur gear whose axis is the frame's Z, its teeth from the origin along Z for `width`, measured on its
        mesh in sections across the axis over the face width: its tip diameter (how far it reaches from the axis, how
        near for an internal gear) and its teeth, counted where the section crosses the circle a module inside the tip
        (outside, for an internal gear), about the pitch circle: twice per tooth."""
        import trimesh

        o, z = fr.origin(frame), fr.z_axis(frame)
        across = np.column_stack([fr.x_axis(frame), np.cross(z, fr.x_axis(frame))])
        tips, counts = [], []
        for share in GEAR_SECTIONS:
            segments = trimesh.intersections.mesh_plane(self.mesh, z, o + z * width * share)
            r = np.linalg.norm((segments - o) @ across, axis=2)  # each segment's ends' distances from the axis
            tip = float(r.min() if internal else r.max()) if len(r) else math.nan
            circle = tip + module if internal else tip - module
            count = int(np.count_nonzero((r[:, 0] > circle) != (r[:, 1] > circle))) // 2
            counts.append(count)
            tips.append(2 * tip)
        kind = "internal" if internal else "external"
        if not any(counts):
            return Measured(status="fail", detail=f"no {kind} gear teeth round the frame's axis over the {width:g} mm "
                            "face width above its origin")  # fmt: skip
        tip = min(tips) if internal else max(tips)
        needs = module * (teeth - 2 if internal else teeth + 2)
        limit = max(tol.position_mm, TIP_SHARE * module)
        problems = []
        if any(c != teeth for c in counts):
            found = " / ".join(str(c) for c in dict.fromkeys(counts))
            problems.append(f"{found} teeth across the face width, {teeth} declared")
        if abs(tip - needs) > limit:
            sign = "−" if internal else "+"
            problems.append(f"tip Ø{tip:.3f}, m{module:g} × ({teeth} {sign} 2) needs Ø{needs:.3f} ± {limit:g}")
        detail = f"{kind} gear m{module:g}, {teeth} teeth, {width:g} mm wide"
        return Measured(status="fail" if problems else "pass",
                        detail="; ".join(problems) or f"{detail}: tip Ø{tip:.3f}",
                        values={"tip_diameter": tip, "teeth": float(counts[len(counts) // 2]), "module": module,
                                "width": width})  # fmt: skip

    @abstractmethod
    def holes(self) -> list[Hole]:
        """Complete round holes (through or blind)."""

    @abstractmethod
    def bodies(self) -> int:
        """Separate solids."""

    @abstractmethod
    def inside_corners(self) -> list[InsideCorner]:
        """Concave corners: sharp edges, and concave cylinders short of a full round hole."""

    @abstractmethod
    def topology(self, kind: str) -> list[Any]:
        """Its "faces", "edges" or "vertices", numbered as the 3D view and picks number them."""

    @abstractmethod
    def exact(self) -> Any:
        """The part as one exact shape: for distances between its faces, edges and vertices, and for STEP."""


# --------------------------------------------------------------------------- B-rep


def _vec(v: Any) -> list[float]:
    return [v.X, v.Y, v.Z]


def flat(shape: Any) -> Any:
    """A shape as one object: a compound of its solids with no children, so viewers and face numbering
    treat it as one part."""
    from build123d import Compound

    solids = shape.solids()
    return Compound(list(solids)) if len(solids) != 1 else solids[0]


class BrepFeatures(Features):
    representation = "B-rep"

    def __init__(
        self, name: str, shape: Any, mesh: Callable[[], trimesh.Trimesh], objects: Callable[[], list[ObjectMesh]]
    ) -> None:
        super().__init__(name, mesh, objects)
        self.shape = shape

    def view(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """One shape with every face, edge and vertex as picks number them, marked `exact` so the panel's
        clicks name them. A viewer shape has one colour: a part made in several shows the colour of most of it."""
        from ocp_tessellate.convert import tessellate_group, to_ocpgroup

        group, instances = to_ocpgroup(flat(self.shape), names=[self.name])
        meshed, shapes, _ = tessellate_group(group, instances)
        (leaf,) = cadview.leaves(shapes)
        colored = [p for p in self.pieces if p.color]
        if colored:
            leaf["color"] = max(colored, key=lambda p: p.mesh.area).color
        return [(meshed[0], {**leaf, "exact": True})]

    def bounds(self) -> np.ndarray:
        box = self.shape.bounding_box()
        return np.array([_vec(box.min), _vec(box.max)])

    def bodies(self) -> int:
        return len(self.shape.solids())

    def topology(self, kind: str) -> list[Any]:
        return getattr(self.shape, kind)()

    def exact(self) -> Any:
        return self.shape

    def inside_corners(self) -> list[InsideCorner]:
        out = []
        faces_of: dict[Any, list[Any]] = defaultdict(list)  # build123d shapes hash by OCC sameness
        for face in self.shape.faces():
            for edge in face.edges():
                faces_of[edge].append(face)
        for edge, faces in faces_of.items():
            if edge.geom_type.name != "LINE" or len(faces) != 2:
                continue
            mid = edge.position_at(0.5)
            if (
                float(faces[0].normal_at(mid).dot(faces[1].center() - mid)) > 0
            ):  # the second face rises on the outer side
                out.append(InsideCorner(0.0, tuple(_vec(edge.tangent_at(0))), edge.length, tuple(_vec(mid))))
        for face in self.shape.faces():
            if face.geom_type.name != "CYLINDER":
                continue
            axis = face.axis_of_rotation
            p = face.position_at(0.5, 0.5)
            radial = (p - axis.position) - axis.direction * (p - axis.position).dot(axis.direction)
            if face.normal_at(p).dot(radial) >= 0:
                continue  # faces away from its axis: convex
            along = [(v.center() - axis.position).dot(axis.direction) for v in face.vertices()] or [0.0]
            extent = max(along) - min(along)
            if extent > 0 and face.area / (face.radius * extent) >= FULL_CIRCLE * 2 * math.pi:
                continue  # a full round hole
            out.append(InsideCorner(face.radius, tuple(_vec(axis.direction)), extent, tuple(_vec(p))))
        return out

    def round(self, frame: fr.Frame, tol: Tolerances, *, inward: bool) -> Measured:
        o, z = fr.origin(frame), fr.z_axis(frame)
        walls: dict[tuple[float, tuple[float, ...]], list[tuple[float, float, float]]] = defaultdict(list)
        placed: dict[tuple[float, ...], tuple[float, float]] = {}  # each wall's axis: (offset, tilt) from the frame's
        reach = []  # extents along the axis of the coaxial faces of revolution: what leads from the origin to a feature
        for face in self.shape.faces():
            if face.geom_type.name not in REVOLVED:
                continue
            axis = face.axis_of_rotation
            d = _vec(axis.direction)
            offset, tilt = fr.distance_to_line(o, _vec(axis.position), d), min(fr.angle_deg(d, z), fr.angle_deg(d, -z))
            ts = [fr.along(_vec(v.center()), o, z) for v in face.vertices()]
            if not ts:
                continue
            if offset <= tol.position_mm and tilt <= tol.angle_deg:
                reach.append((min(ts), max(ts), 0.0))
            if face.geom_type.name == "CYLINDER" and _faces_axis(face) == inward:
                key = _axis_key(axis)
                placed[key] = (offset, tilt)
                walls[(round(face.radius, 6), key)].append((min(ts), max(ts), face.area))
        found = [
            _Found(2 * r, hi - lo, -hi if inward else lo, *placed[key], area / (2 * math.pi * r * (hi - lo)))
            for (r, key), extents in walls.items()
            for lo, hi, area in _touching(extents, tol.position_mm)
            if hi > lo
        ]
        merged = _touching(reach, tol.position_mm)

        def leads(gap: float) -> bool:
            lo, hi = (-gap, 0.0) if inward else (0.0, gap)
            return any(a <= lo + tol.position_mm and b >= hi - tol.position_mm for a, b, _ in merged)

        return _pick(found, leads, inward, tol)

    def face(self, frame: fr.Frame, tol: Tolerances) -> Measured:
        from build123d import Face, Vertex

        o, z = fr.origin(frame), fr.z_axis(frame)
        point = Vertex(*o)
        for face in self.shape.faces():
            if not face.is_planar or fr.angle_deg(_vec(face.normal_at()), z) > tol.angle_deg:
                continue
            if abs(fr.along(_vec(face.center()), o, z)) > tol.position_mm:
                continue
            if Face(face.outer_wire()).distance_to(point) > tol.position_mm:
                continue
            return _face(face.area)
        return _no_face()

    def holes(self) -> list[Hole]:
        return self._holes

    @cached_property
    def _holes(self) -> list[Hole]:
        from build123d import Vector

        groups: dict[tuple[float, tuple[float, ...]], list] = defaultdict(list)
        for face in self.shape.faces():
            if face.geom_type.name == "CYLINDER" and _faces_axis(face):  # not a boss or a rounded outside corner
                groups[(round(face.radius, 4), _axis_key(face.axis_of_rotation))].append(face)
        holes = []
        for (radius, key), faces in groups.items():
            origin, direction = Vector(*key[3:]), Vector(*key[:3])
            along = [(v.center() - origin).dot(direction) for f in faces for v in f.vertices()]
            depth = max(along) - min(along)
            if depth > 0 and sum(f.area for f in faces) / (radius * depth) >= FULL_CIRCLE * 2 * math.pi:
                holes.append(Hole(diameter=2 * radius, depth=depth))
        return holes


def _faces_axis(face: Any) -> bool:
    """Whether a face of revolution faces its axis: the wall of a hole, not the skin of a shaft."""
    axis = face.axis_of_rotation
    p = face.position_at(0.5, 0.5)
    radial = (p - axis.position) - axis.direction * (p - axis.position).dot(axis.direction)
    return face.normal_at(p).dot(radial) < 0


def _axis_key(axis: Any) -> tuple[float, ...]:
    """A line by value: its direction (pointing up its first nonzero coordinate) and its point nearest the origin."""
    d = axis.direction.normalized()
    direction = [round(c, 6) + 0.0 for c in (d.X, d.Y, d.Z)]
    if next(c for c in direction if c != 0) < 0:
        direction = [-c + 0.0 for c in direction]
    p = axis.position - d * axis.position.dot(d)
    return (*direction, *(round(c, 3) + 0.0 for c in (p.X, p.Y, p.Z)))


def _touching(extents: list[tuple[float, float, float]], tol: float) -> list[tuple[float, float, float]]:
    """Extents along an axis with an area each, those that touch merged: one coaxial feature each, not every hole on
    the axis."""
    merged: list[list[float]] = []
    for lo, hi, area in sorted(extents):
        if merged and lo <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], hi)
            merged[-1][2] += area
        else:
            merged.append([lo, hi, area])
    return [(lo, hi, area) for lo, hi, area in merged]


# --------------------------------------------------------------------------- mesh

#: Rays cast round the axis at each station along it.
RAYS = 32
#: Rays land on a round surface when their distances to the axis spread less than this share of the radius.
ROUNDNESS = 0.05
#: Stations along the axis are this far apart (mm).
STEP_MM = 0.2
#: Bisections that place where a feature starts and ends: to within STEP_MM / 2⁸.
BISECTIONS = 8


@dataclass(frozen=True)
class _Station:
    """Where the rays at one station along a frame's axis land, as a circle fitted through the vertices of the facets
    they land on, in the plane across the axis: so it is the same however the frame is turned."""

    radius: float
    centre: np.ndarray  # from the frame's axis
    residual: float  # how far the farthest of those vertices lies off the circle: none on a cylinder, more on a cone
    sagitta: float  # how far inside the circle the facets' chords cut
    share: float  # of the rays, those that land on the circle


def _circle(points: np.ndarray) -> tuple[np.ndarray, float]:
    """The least-squares circle through 2D points: (centre, radius)."""
    a = np.column_stack([2 * points, np.ones(len(points))])
    (cx, cy, k), *_ = np.linalg.lstsq(a, (points**2).sum(axis=1), rcond=None)
    centre = np.array([cx, cy])
    return centre, math.sqrt(max(k + cx * cx + cy * cy, 0.0))


class MeshFeatures(Features):
    """Round features are probed with rays in sections across the frame's axis, at stations along it. A hole is
    probed from the axis outwards: the rays land on its wall, facing the axis. A shaft is probed from outside
    inwards: the rays land on its skin, facing away, even where it is a tube. Where most land at one distance from
    the axis, the station is on a round surface; where the vertices of the facets they land on lie on one circle,
    on a cylinder, whose diameter and centre that circle gives, and whose uncertainty is the facets' sagitta. A
    feature runs while its stations stay on one cylinder. Faces are trimesh's facets (coplanar neighbouring
    triangles) and the single triangles outside them."""

    representation = "mesh"

    def view(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """One shape per colour, each with its crease and boundary lines as edges."""
        return [cadview.mesh_entry(p.mesh, p.name.replace("/", "_"), p.color) for p in self.pieces]

    def bounds(self) -> np.ndarray:
        return self.mesh.bounds

    def holes(self) -> list[Hole]:
        raise Unmeasurable(f"holes are counted on B-rep faces; {self.name} is a mesh")

    def bodies(self) -> int:
        return self.mesh.body_count

    def inside_corners(self) -> list[InsideCorner]:
        raise Unmeasurable(f"inside corner radii are measured on B-rep faces; {self.name} is a mesh")

    def topology(self, kind: str) -> list[Any]:
        raise Unmeasurable(f"{self.name} is a mesh: it has no faces, edges or vertices; pick a point on it")

    def exact(self) -> Any:
        raise Unmeasurable(f"{self.name} is a mesh: it has no exact shape")

    def round(self, frame: fr.Frame, tol: Tolerances, *, inward: bool) -> Measured:
        import trimesh

        o, z = fr.origin(frame), fr.z_axis(frame)
        across = np.column_stack([fr.x_axis(frame), np.cross(z, fr.x_axis(frame))])
        angles = np.linspace(0, 2 * np.pi, RAYS, endpoint=False)
        rays = np.column_stack([np.cos(angles), np.sin(angles)])
        mesh, close = self.mesh, tol.position_mm
        heights = (mesh.vertices - o) @ z
        lowest, highest = heights[mesh.faces].min(axis=1), heights[mesh.faces].max(axis=1)
        reach = float(highest.max() if not inward else -lowest.min())

        def station(t: float) -> _Station | None:
            """The round surface the rays from the axis (a hole's) or from outside (a shaft's) land on at `t` along
            it, in the section across the axis there, or None where they do not land at one distance from it."""
            h = -t if inward else t
            near = np.flatnonzero((lowest <= h) & (highest >= h))
            lines, faces = trimesh.intersections.mesh_plane(mesh, z, o + z * h, return_faces=True, local_faces=near,
                                                            cached_dots=heights - h)  # fmt: skip
            if not len(lines):
                return None
            # where each ray meets each segment of the section: r·u = p + w·(q − p), in the plane
            p, d = (lines[:, 0] - o) @ across, (lines[:, 1] - lines[:, 0]) @ across
            with np.errstate(divide="ignore", invalid="ignore"):  # segments parallel to a ray never meet it
                det = np.outer(rays[:, 0], d[:, 1]) - np.outer(rays[:, 1], d[:, 0])
                r = (p[:, 0] * d[:, 1] - p[:, 1] * d[:, 0]) / det
                w = (np.outer(rays[:, 1], p[:, 0]) - np.outer(rays[:, 0], p[:, 1])) / det
            r = np.where((w >= 0) & (w <= 1) & (r > 0), r, np.nan)
            landed = ~np.isnan(r).all(axis=1)
            if np.count_nonzero(landed) < ON_CIRCLE * RAYS:
                return None
            # the first surface each ray meets: from the axis outwards for a hole, from outside inwards for a shaft
            pick = (np.nanargmin if inward else np.nanargmax)(r[landed], axis=1)
            hit, distance = faces[pick], r[landed][np.arange(len(pick)), pick]
            flat = rays[landed] * distance[:, None]
            facing = np.einsum("ij,ij->i", mesh.face_normals[hit] @ across, rays[landed])
            keep = facing < 0 if inward else facing > 0
            hit, flat, distance = hit[keep], flat[keep], distance[keep]
            if len(hit) < ON_CIRCLE * RAYS:
                return None
            # A hole's wall is the nearest the rays land, a shaft's skin the farthest; an opening's flanks take a
            # hole's landings out.
            if inward:
                band = distance <= distance.min() * (1 + ROUNDNESS)
                band &= distance <= np.percentile(distance[band], 100 * ON_CIRCLE)
            else:
                band = distance >= distance.max() * (1 - ROUNDNESS)
            if np.count_nonzero(band) < ON_CIRCLE**2 * RAYS:
                return None
            corners = (mesh.vertices[mesh.faces[np.unique(hit[band])]] - o) @ across  # (facets, 3, 2)
            c, r = _circle(corners.reshape(-1, 2))
            off = np.abs(np.linalg.norm(corners - c, axis=2) - r).max(axis=1)
            if (off > close).any() and (off <= close).any():  # an opening's flank facets: fit the wall alone
                corners = corners[off <= close]
                c, r = _circle(corners.reshape(-1, 2))
                off = np.abs(np.linalg.norm(corners - c, axis=2) - r).max(axis=1)
            ends = (corners - c) / r
            chord = float(np.linalg.norm(ends - np.roll(ends, 1, axis=1), axis=2).max())  # longest, on a unit circle
            sagitta = r * (1 - math.sqrt(max(1 - chord * chord / 4, 0.0)))
            on = np.abs(np.linalg.norm(flat - c, axis=1) - r) <= sagitta + close
            return _Station(r, c, float(off.max()), sagitta, np.count_nonzero(on) / RAYS)

        def wall(s: _Station | None) -> bool:
            return s is not None and s.residual <= close and s.share >= ON_CIRCLE

        step = STEP_MM
        start, first = next(((t, s) for t in np.arange(step / 2, reach, step) if wall(s := station(t))), (0.0, None))
        if first is None:
            return _no_round(inward, None, tol)

        def on(t: float) -> bool:
            """Still the first station's wall."""
            s = station(t)
            if s is None or not wall(s):
                return False
            return abs(s.radius - first.radius) <= close and float(np.linalg.norm(s.centre - first.centre)) <= close

        def edge(inside: float, outside: float) -> float:
            for _ in range(BISECTIONS):
                middle = (inside + outside) / 2
                inside, outside = (middle, outside) if on(middle) else (inside, middle)
            return inside

        end = start
        while end + step < reach and on(end + step):
            end += step
        t0, t1 = edge(start, max(start - step, 0.0)), edge(end, end + step)
        last = station(end)
        drift = float(np.linalg.norm(last.centre - first.centre)) if last and end > start else 0.0
        found = _Found(2 * first.radius, t1 - t0, t0, float(np.linalg.norm(first.centre)),
                       math.degrees(math.atan2(drift, end - start)), first.share, 2 * first.sagitta)  # fmt: skip

        def leads(gap: float) -> bool:
            """Round surfaces about the axis all the way from the origin to the feature: a countersink, a chamfer."""
            return all(station(t) is not None for t in np.linspace(0, gap, math.ceil(gap / step) + 2)[1:-1])

        return _pick([found], leads, inward, tol)

    def face(self, frame: fr.Frame, tol: Tolerances) -> Measured:
        o, z = fr.origin(frame), fr.z_axis(frame)
        x = fr.x_axis(frame)
        y = np.cross(z, x)
        mesh = self.mesh
        aligned = mesh.face_normals @ z >= math.cos(math.radians(tol.angle_deg))
        on_plane = np.abs((mesh.triangles[:, 0] - o) @ z) <= tol.position_mm
        candidates = aligned & on_plane
        grouped = [f for f in self._regions if candidates[f[0]]]
        for faces in grouped:
            outline = _outer_loop(mesh, faces)
            flat = (mesh.vertices[outline] - o) @ np.column_stack([x, y])
            if _inside(flat, tol.position_mm):
                return _face(float(mesh.area_faces[faces].sum()))
        return _no_face()

    @cached_property
    def _regions(self) -> list[np.ndarray]:
        """Planar regions: trimesh's facets and every triangle outside them."""
        grouped = np.zeros(len(self.mesh.faces), dtype=bool)
        for facet in self.mesh.facets:
            grouped[facet] = True
        return [*self.mesh.facets, *(np.array([f]) for f in np.flatnonzero(~grouped))]


def _outer_loop(mesh: trimesh.Trimesh, faces: np.ndarray) -> np.ndarray:
    """The edges (vertex index pairs) of a planar region's outer outline: the boundary loop that spans
    the most, so an annulus's outline is its rim, not its hole."""
    import trimesh

    edges = np.sort(mesh.faces[faces][:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    boundary = edges[trimesh.grouping.group_rows(edges, require_count=1)]
    loops = trimesh.graph.connected_components(boundary, min_len=3)
    spans = [float(np.ptp(mesh.vertices[loop], axis=0).sum()) for loop in loops]
    outer = set(loops[int(np.argmax(spans))])
    return boundary[[a in outer for a, _ in boundary]]


def _inside(segments: np.ndarray, tol: float) -> bool:
    """Whether the origin is inside the outline of 2D segments (n, 2, 2), or within `tol` of it."""
    a, b = segments[:, 0], segments[:, 1]
    crosses = (a[:, 1] > 0) != (b[:, 1] > 0)
    x_at = a[crosses, 0] - a[crosses, 1] * (b[crosses, 0] - a[crosses, 0]) / (b[crosses, 1] - a[crosses, 1])
    if np.count_nonzero(x_at > 0) % 2:
        return True
    ab = b - a
    t = np.clip(-np.einsum("ij,ij->i", a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-12), 0, 1)
    return float(np.linalg.norm(a + ab * t[:, None], axis=1).min()) <= tol


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class _Found:
    """A round feature by a frame's axis: its diameter, its length along the axis from where it starts (`gap` past the
    origin, into the part), how far its axis lies from the frame's and how much of the circle its wall goes round."""

    diameter: float
    length: float
    gap: float
    offset: float
    tilt: float
    share: float
    uncertainty: float = 0.0


def _pick(found: list[_Found], leads: Callable[[float], bool], inward: bool, tol: Tolerances) -> Measured:
    """The frame's feature among those found: coaxial, round, the one that starts closest past the origin (of those
    starting equally close, the innermost hole or the outermost shaft). It starts at the origin, or farther only
    where coaxial surfaces of revolution lead to it from the origin (`leads`: a countersink, a chamfer). With none,
    the result names the nearest feature found and what keeps it from being the frame's."""
    fits = [f for f in found if f.offset <= tol.position_mm and f.tilt <= tol.angle_deg and f.share >= ON_CIRCLE
            and f.gap >= -tol.position_mm]  # fmt: skip
    if fits:
        closest = min(f.gap for f in fits)
        tied = [f for f in fits if f.gap <= closest + tol.position_mm]
        hit = (min if inward else max)(tied, key=lambda f: f.diameter)
        if hit.gap <= tol.position_mm or leads(hit.gap):
            return _round(inward, hit, tol)
        return _no_round(inward, hit, tol)
    return _no_round(inward, min(found, key=lambda f: f.offset + abs(f.gap), default=None), tol)


def _round(inward: bool, f: _Found, tol: Tolerances) -> Measured:
    gap = f.gap if f.gap > tol.position_mm else 0.0
    detail = f"Ø{f.diameter:.3f} × {f.length:.2f} {'deep' if inward else 'long'}"
    if f.uncertainty:
        detail += f" (on the mesh, ±{f.uncertainty:.3f})"
    if gap:
        detail += f", starting {gap:.2f} mm from the origin"
    return Measured(status="pass", detail=detail, values={"diameter": f.diameter, "length": f.length, "offset": gap},
                    uncertainty_mm=f.uncertainty)  # fmt: skip


def _no_round(inward: bool, nearest: _Found | None, tol: Tolerances) -> Measured:
    what, side = ("hole", "below") if inward else ("shaft", "above")
    detail = f"no {what} on the frame's axis {side} its origin"
    if nearest is not None:
        f, limit = nearest, tol.position_mm
        notes = [f"axis {f.offset:.3f} mm off" + (f" (allowed {limit:g})" if f.offset > limit else ""),
                 f"{f.tilt:.2f}° tilt" + (f" (allowed {tol.angle_deg:g})" if f.tilt > tol.angle_deg else "")]  # fmt: skip
        if f.share < ON_CIRCLE:
            notes.append(f"its wall goes {360 * f.share:.0f}° round, not a full circle")
        if f.gap > limit:
            notes.append(f"starts {f.gap:.2f} mm {side} the origin with no coaxial surface leading to it")
        elif f.gap < -limit:
            notes.append(f"lies {'above' if inward else 'below'} the origin")
        detail += f"; nearest: Ø{f.diameter:.3f} {what}, {', '.join(notes)}"
    return Measured(status="fail", detail=detail)


def _face(area: float) -> Measured:
    return Measured(status="pass", detail=f"face of {area:.1f} mm²", values={"area": area})


def _no_face() -> Measured:
    return Measured(
        status="fail", detail="no planar face with Z as its outward normal and the origin inside its outline"
    )
