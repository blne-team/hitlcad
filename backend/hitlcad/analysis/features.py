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

    @abstractmethod
    def holes(self) -> list[Hole]:
        """Complete round holes (through or blind)."""

    @abstractmethod
    def bodies(self) -> int:
        """Separate solids."""

    @abstractmethod
    def inside_corners(self, tol: Tolerances) -> list[InsideCorner]:
        """Concave corners: sharp edges, and concave cylinders short of a full round hole."""

    @abstractmethod
    def bends(self, thickness: float, slack: float, tol: Tolerances) -> list[InsideCorner]:
        """A sheet's bends, by their inside radius: coaxial cylinders, inside r and outside r + `thickness`
        (± `slack`), and sharp (radius 0) concave edges between two faces that each have the sheet's other side
        `thickness` behind them, unlike the corners of a cut-out."""

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

    def inside_corners(self, tol: Tolerances) -> list[InsideCorner]:
        return [_sharp(edge) for edge, _ in self._concave_edges(tol)] + [
            corner for corner, convex, _ in self._cylinders() if not convex
        ]

    def bends(self, thickness: float, slack: float, tol: Tolerances) -> list[InsideCorner]:
        cylinders = self._cylinders()
        outside = [(c.radius, axis) for c, convex, axis in cylinders if convex]
        rounded = [
            corner
            for corner, convex, axis in cylinders
            if not convex
            and any(abs(r - corner.radius - thickness) <= slack and _coaxial(axis, a, tol) for r, a in outside)
        ]
        faces = self.shape.faces()
        sharp = [
            _sharp(edge)
            for edge, pair in self._concave_edges(tol)
            if all(_sheet_behind(face, faces, thickness, slack, tol) for face in pair)
        ]
        return rounded + sharp

    def _concave_edges(self, tol: Tolerances) -> list[tuple[Any, list[Any]]]:
        """Straight edges where two faces meet in a concave corner, with the two faces. Concave is decided at the
        edge: the faces' normals there turn about the edge, as it runs in the first face, the concave way.
        Faces meeting tangentially (a fillet's edge) make no corner."""
        faces_of: dict[Any, list[Any]] = defaultdict(list)  # build123d shapes hash by OCC sameness
        for face in self.shape.faces():
            for edge in face.edges():  # the key is the edge as it runs in the first face that has it
                faces_of[edge].append(face)
        out = []
        for edge, faces in faces_of.items():
            if edge.geom_type.name != "LINE" or len(faces) != 2:
                continue
            mid = edge.position_at(0.5)
            first, second = (face.normal_at(mid) for face in faces)
            if first.get_angle(second) <= tol.angle_deg:
                continue
            if first.cross(second).dot(edge.tangent_at(0.5)) < 0:
                out.append((edge, faces))
        return out

    def _cylinders(self) -> list[tuple[InsideCorner, bool, tuple[Any, Any]]]:
        """Each cylindrical face short of a full round hole: as a corner, whether it is convex (faces away from
        its axis), and its axis (a point and a direction)."""
        out = []
        for face in self.shape.faces():
            if face.geom_type.name != "CYLINDER":
                continue
            axis = face.axis_of_rotation
            p = face.position_at(0.5, 0.5)
            radial = (p - axis.position) - axis.direction * (p - axis.position).dot(axis.direction)
            convex = face.normal_at(p).dot(radial) >= 0
            along = [(v.center() - axis.position).dot(axis.direction) for v in face.vertices()] or [0.0]
            extent = max(along) - min(along)
            if not convex and extent > 0 and face.area / (face.radius * extent) >= FULL_CIRCLE * 2 * math.pi:
                continue  # a full round hole
            corner = InsideCorner(face.radius, tuple(_vec(axis.direction)), extent, tuple(_vec(p)))
            out.append((corner, convex, (axis.position, axis.direction)))
        return out

    def round(self, frame: fr.Frame, tol: Tolerances, *, inward: bool) -> Measured:
        o, z = fr.origin(frame), fr.z_axis(frame)
        faces: dict[float, list[tuple[float, float]]] = defaultdict(list)
        for face in self.shape.faces():
            if face.geom_type.name != "CYLINDER":
                continue
            axis = face.axis_of_rotation
            d = _vec(axis.direction)
            if min(fr.angle_deg(d, z), fr.angle_deg(d, -z)) > tol.angle_deg:
                continue
            if fr.distance_to_line(o, _vec(axis.position), d) > tol.position_mm:
                continue
            p = face.position_at(0.5, 0.5)
            radial = (p - axis.position) - axis.direction * (p - axis.position).dot(axis.direction)
            if (face.normal_at(p).dot(radial) < 0) != inward:
                continue
            ts = [fr.along(_vec(v.center()), o, z) for v in face.vertices()]
            if ts:
                faces[round(face.radius, 6)].append((min(ts), max(ts)))
        spans = [(r, s) for r, extents in faces.items() for s in _touching(extents, tol.position_mm)]
        if inward:
            near = [(r, s, -s[1]) for r, s in spans if s[1] <= tol.position_mm]
        else:
            near = [(r, s, s[0]) for r, s in spans if s[0] >= -tol.position_mm]
        closest = min((gap for _, _, gap in near), default=None)
        tied = [(r, s) for r, s, gap in near if closest is not None and gap <= closest + tol.position_mm]
        hit = (min if inward else max)(tied, key=lambda rs: rs[0], default=None)
        if hit is None:
            return _no_round(inward)
        radius, (t0, t1) = hit
        return _round(inward, 2 * radius, t1 - t0, -t1 if inward else t0, 0.0, tol)

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

        groups: dict[tuple[float, tuple[float, ...], tuple[float, ...]], list] = defaultdict(list)
        for face in self.shape.faces():
            if face.geom_type.name != "CYLINDER":
                continue
            axis = face.axis_of_rotation
            d = axis.direction.normalized()
            p = face.position_at(0.5, 0.5)
            radial = (p - axis.position) - d * (p - axis.position).dot(d)
            if face.normal_at(p).dot(radial) >= 0:
                continue  # faces away from its axis: a boss or a rounded outside corner
            direction = tuple(round(c, 6) + 0.0 for c in (d.X, d.Y, d.Z))
            if next(c for c in direction if c != 0) < 0:
                direction = tuple(-c + 0.0 for c in direction)
            origin = axis.position - d * axis.position.dot(d)
            groups[
                (round(face.radius, 4), direction, tuple(round(c, 3) + 0.0 for c in (origin.X, origin.Y, origin.Z)))
            ].append(face)
        holes = []
        for (radius, direction, origin), faces in groups.items():
            along = [(v.center() - Vector(*origin)).dot(Vector(*direction)) for f in faces for v in f.vertices()]
            depth = max(along) - min(along)
            if depth > 0 and sum(f.area for f in faces) / (radius * depth) >= FULL_CIRCLE * 2 * math.pi:
                holes.append(Hole(diameter=2 * radius, depth=depth))
        return holes


def _sharp(edge: Any) -> InsideCorner:
    return InsideCorner(0.0, tuple(_vec(edge.tangent_at(0))), edge.length, tuple(_vec(edge.position_at(0.5))))


def _coaxial(a: tuple[Any, Any], b: tuple[Any, Any], tol: Tolerances) -> bool:
    """Whether two axes (point, direction) are one line."""
    (pa, da), (pb, db) = a, b
    if min(fr.angle_deg(_vec(da), _vec(db)), fr.angle_deg(_vec(da), _vec(-db))) > tol.angle_deg:
        return False
    return fr.distance_to_line(_vec(pb), _vec(pa), _vec(da)) <= tol.position_mm


def _sheet_behind(face: Any, faces: list[Any], thickness: float, slack: float, tol: Tolerances) -> bool:
    """Whether a planar face has the sheet's other side behind it: a planar face turned the other way,
    `thickness` (± `slack`) deeper into the part."""
    if not face.is_planar:
        return False
    normal, at = face.normal_at(), face.center()
    return any(
        other.is_planar
        and fr.angle_deg(_vec(other.normal_at()), _vec(-normal)) <= tol.angle_deg
        and abs((at - other.center()).dot(normal) - thickness) <= slack
        for other in faces
    )


def _touching(extents: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Extents along an axis, those that touch merged: one coaxial feature each, not every hole on the axis."""
    merged: list[list[float]] = []
    for lo, hi in sorted(extents):
        if merged and lo <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


# --------------------------------------------------------------------------- mesh

#: Rays cast round the axis at each station along it.
RAYS = 32
#: Of those, the share that must land on the circle: the rest may leave through an opening in its wall
#: (a teardrop's point, a slot, a screw hole beside a shaft).
ON_CIRCLE = 0.6
#: Rays land on the circle when their distances to the axis spread less than this share of the radius:
#: a polygon of 12 or more sides passes, a square or an off-axis bore does not.
ROUNDNESS = 0.05
#: Stations along the axis are this far apart (mm): a run shorter than two of them is the slice of a
#: cone or a chamfer, not a feature.
STEP_MM = 0.2
#: Bisections that place where a feature starts and ends.
BISECTIONS = 20


class MeshFeatures(Features):
    """Round features are probed with rays at stations along the frame's axis. A hole is probed from the
    axis outwards: the rays land on its wall, facing the axis. A shaft is probed from outside inwards: the
    rays land on its skin, facing away, even where it is a tube. Where most land at one distance from the
    axis, the station is on the feature; a feature runs while its stations do. Faces are trimesh's facets
    (coplanar neighbouring triangles) and the single triangles outside them."""

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

    def inside_corners(self, tol: Tolerances) -> list[InsideCorner]:
        raise Unmeasurable(f"inside corner radii are measured on B-rep faces; {self.name} is a mesh")

    def bends(self, thickness: float, slack: float, tol: Tolerances) -> list[InsideCorner]:
        raise Unmeasurable(f"bends are measured on B-rep faces; {self.name} is a mesh")

    def topology(self, kind: str) -> list[Any]:
        raise Unmeasurable(f"{self.name} is a mesh: it has no faces, edges or vertices; pick a point on it")

    def exact(self) -> Any:
        raise Unmeasurable(f"{self.name} is a mesh: it has no exact shape")

    def round(self, frame: fr.Frame, tol: Tolerances, *, inward: bool) -> Measured:
        o, z = fr.origin(frame), fr.z_axis(frame)
        x = fr.x_axis(frame)
        angles = np.linspace(0, 2 * np.pi, RAYS, endpoint=False)
        out = np.outer(np.cos(angles), x) + np.outer(np.sin(angles), np.cross(z, x))
        along = -z if inward else z
        reach = float(np.linalg.norm(self.mesh.extents))

        def station(t: float) -> np.ndarray | None:
            """The distances to the axis of the rays that land on a circle at `t` along it, or None where
            too few do."""
            centre = o + along * t
            origins = np.tile(centre, (RAYS, 1)) if inward else centre + out * reach
            with np.errstate(divide="ignore", invalid="ignore"):  # trimesh's test of sliver triangles, never hit
                hit, ray, at = self.mesh.ray.intersects_id(origins, out if inward else -out, multiple_hits=False,
                                                           return_locations=True)  # fmt: skip
            if len(ray) < ON_CIRCLE * RAYS:
                return None
            radial = at - centre
            facing = np.einsum("ij,ij->i", self.mesh.face_normals[hit], radial)
            distance = np.linalg.norm(radial, axis=1)[facing < 0 if inward else facing > 0]
            if len(distance) < ON_CIRCLE * RAYS:
                return None
            # A hole's wall is the nearest the rays land, a shaft's skin the farthest; facets bring their
            # landings in by at most their sagitta, an opening's flanks take a hole's out.
            if inward:
                band = distance[distance <= distance.min() * (1 + ROUNDNESS)]
                band = band[band <= np.percentile(band, 100 * ON_CIRCLE)]
            else:
                band = distance[distance >= distance.max() * (1 - ROUNDNESS)]
            return band if len(band) >= ON_CIRCLE**2 * RAYS else None

        step = STEP_MM

        def run(start: float) -> tuple[float, float, np.ndarray] | None:
            """The feature found at `start`, as (where it starts, where it ends, its first station)."""
            first = station(start)
            assert first is not None
            radius = float(first.max())

            def on(t: float) -> bool:
                d = station(t)
                return d is not None and abs(float(d.max()) - radius) <= max(float(np.ptp(first)), tol.position_mm)

            def edge(inside: float, outside: float) -> float:
                for _ in range(BISECTIONS):
                    middle = (inside + outside) / 2
                    inside, outside = (middle, outside) if on(middle) else (inside, middle)
                return inside

            end = start
            while end + step < reach and on(end + step):
                end += step
            if end == start:
                return None  # a slice of a cone or a chamfer, not a feature
            return edge(start, max(start - step, 0.0)), edge(end, end + step), first

        found = next(
            (r for t in np.arange(step / 2, reach, step) if station(t) is not None and (r := run(t)) is not None), None
        )
        if found is None:
            return _no_round(inward)
        t0, t1, first = found
        if t0 <= tol.position_mm:
            t0 = 0.0
        return _round(inward, 2 * float(first.max()), t1 - t0, t0, 2 * float(np.ptp(first)), tol)

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


def _round(inward: bool, diameter: float, length: float, gap: float, uncertainty: float, tol: Tolerances) -> Measured:
    detail = f"Ø{diameter:.3f} × {length:.2f} {'deep' if inward else 'long'}"
    if uncertainty:
        detail += f" (on the mesh, ±{uncertainty:.3f})"
    if gap > tol.position_mm:
        detail += f", starting {gap:.2f} mm from the origin"
    return Measured(status="pass", detail=detail, values={"diameter": diameter, "length": length, "offset": gap},
                    uncertainty_mm=uncertainty)  # fmt: skip


def _no_round(inward: bool) -> Measured:
    what, side = ("hole", "below") if inward else ("shaft", "above")
    return Measured(status="fail", detail=f"no {what} on the frame's axis {side} its origin")


def _face(area: float) -> Measured:
    return Measured(status="pass", detail=f"face of {area:.1f} mm²", values={"area": area})


def _no_face() -> Measured:
    return Measured(
        status="fail", detail="no planar face with Z as its outward normal and the origin inside its outline"
    )
